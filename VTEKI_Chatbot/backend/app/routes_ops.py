"""Endpoint operasional: tiket CRM, handoff, Copilot, bukti, kanal email, analitik & QA.

Dipisah dari main.py agar modul inti chat tetap ringkas. Helper bersama (kasus, rate limit,
notifikasi, pemrosesan pesan) diambil dari main.py saat dipanggil.
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import copilot, crm, evidence, knowledge, taxonomy
from .config import EVIDENCE_MAX_MB, RATE_UPLOAD_PER_MIN
from .db import connect
from .security import admin_only, audit, current_user, hash_password, supervisor_only

router = APIRouter()


def _m():
    from . import main
    return main


class TransitionBody(BaseModel):
    to: str
    note: str = ''


class CloseTicketBody(BaseModel):
    checklist: dict = {}
    root_cause: str = ''
    note: str = ''


class QABody(BaseModel):
    scores: dict
    notes: str = ''


class EmailInboundBody(BaseModel):
    from_email: str = Field(min_length=5, max_length=200)
    subject: str = Field(default='', max_length=200)
    body: str = Field(min_length=1, max_length=4000)


def _crm_call(fn, *a, **kw):
    try:
        with connect() as c:
            return fn(c, *a, **kw)
    except crm.CRMError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# ---------------------------------------------------------------------------
# CRM: tiket (UC-03) & state machine
# ---------------------------------------------------------------------------
@router.get('/api/crm/meta')
def crm_meta(user=Depends(admin_only)):
    return {'transitions': {k: sorted(v) for k, v in crm.TRANSITIONS.items()}, 'checklist': crm.CHECKLIST,
            'root_causes': crm.ROOT_CAUSES, 'qa_criteria': crm.QA_CRITERIA, 'handoff_fields': crm.HANDOFF_FIELDS,
            'intents': [{'code': k, 'label': v[0], 'agent': taxonomy.AGENT_LABEL.get(v[1], v[1]), 'min_al': v[2], 'ticket': v[3]}
                        for k, v in taxonomy.INTENTS.items()]}


@router.get('/api/admin/tickets')
def list_tickets(status: str = '', user=Depends(admin_only)):
    sql = 'SELECT t.*, u.name customer_name FROM tickets t JOIN users u ON u.id=t.user_id'
    with connect() as c:
        rows = c.execute(sql + (' WHERE t.status=?' if status else '') + ' ORDER BY t.updated_at DESC',
                         (status,) if status else ()).fetchall()
    return {'items': [crm.ticket_dict(r) for r in rows]}


@router.get('/api/admin/tickets/{ticket_id}')
def get_ticket(ticket_id: str, user=Depends(admin_only)):
    with connect() as c:
        tk = c.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if not tk:
            raise HTTPException(404, 'Tiket tidak ditemukan.')
        events = [dict(r) for r in c.execute('SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id', (ticket_id,))]
    return {'ticket': crm.ticket_dict(tk), 'events': events}


@router.post('/api/admin/tickets/{ticket_id}/transition')
def ticket_transition(ticket_id: str, body: TransitionBody, user=Depends(admin_only)):
    if body.to == 'Selesai':
        raise HTTPException(400, 'Gunakan penutupan tiket dengan closure checklist untuk status Selesai.')
    tk = _crm_call(crm.transition, ticket_id, body.to, user['email'], body.note)
    audit(user['email'], f'Tiket {ticket_id} -> {body.to}')
    return crm.ticket_dict(tk)


@router.post('/api/admin/tickets/{ticket_id}/close')
def ticket_close(ticket_id: str, body: CloseTicketBody, user=Depends(admin_only)):
    tk = _crm_call(crm.close_ticket, ticket_id, actor=user['email'], checklist=body.checklist,
                   root_cause=body.root_cause, note=body.note)
    with connect() as c:
        c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)',
                  (tk['user_id'], tk['case_id'], f"Tiket {ticket_id} ({tk['category']}) telah diselesaikan."))
    audit(user['email'], f'Menutup tiket {ticket_id} (akar masalah: {body.root_cause})')
    return crm.ticket_dict(tk)


# ---------------------------------------------------------------------------
# Handoff (UC-05) & Copilot (UC-06)
# ---------------------------------------------------------------------------
@router.get('/api/admin/cases/{case_id}/workspace')
def case_workspace(case_id: str, user=Depends(admin_only)):
    """Workspace agen: tiket, paket handoff 17 field, dan bukti (teks bukti hanya untuk manusia)."""
    _m()._visible_case(case_id, user)
    with connect() as c:
        tickets = [crm.ticket_dict(r) for r in c.execute('SELECT * FROM tickets WHERE case_id=? ORDER BY created_at DESC', (case_id,))]
        handoffs = [{'id': r['id'], 'reason': r['reason'], 'created_at': r['created_at'], 'accepted_by': r['accepted_by'],
                     'package': json.loads(r['package'])}
                    for r in c.execute('SELECT * FROM handoffs WHERE case_id=? ORDER BY id DESC', (case_id,))]
        ev = [dict(r) for r in c.execute("""SELECT id,filename,mime,size,status,scanner,scan_result,text_excerpt,reviewed_by,
                                            created_at,scanned_at FROM evidence WHERE case_id=? ORDER BY id""", (case_id,))]
    return {'tickets': tickets, 'handoffs': handoffs, 'evidence': ev}


@router.get('/api/admin/handoffs')
def handoff_queue(user=Depends(admin_only)):
    """Antrean handoff yang belum diambil agen, urut prioritas lalu waktu."""
    with connect() as c:
        rows = c.execute("""SELECT h.* FROM handoffs h JOIN cases c ON c.id=h.case_id
                            WHERE h.accepted_by IS NULL AND c.status NOT IN ('Selesai','Ditolak') ORDER BY h.id""").fetchall()
    items = []
    for r in rows:
        p = json.loads(r['package'])
        items.append({'id': r['id'], 'handoff_id': p['handoff_id'], 'case_id': r['case_id'], 'ticket_id': r['ticket_id'],
                      'reason': p['handoff_reason']['label'], 'priority': p['priority_sla']['priority'],
                      'sla_due_at': p['priority_sla']['sla_due_at'], 'intent': p['intent']['label'], 'channel': p['channel'],
                      'auth_level': p['auth_level'], 'created_at': r['created_at']})
    items.sort(key=lambda x: (-crm.PRIO_RANK.get(x['priority'], 1), x['created_at']))
    return {'items': items}


@router.post('/api/admin/cases/{case_id}/copilot')
def copilot_draft(case_id: str, user=Depends(admin_only)):
    _m()._visible_case(case_id, user)
    d = copilot.draft(case_id, knowledge.current_org())
    audit(user['email'], f'Meminta draf Copilot untuk {case_id}' + (' (LLM)' if d['used_llm'] else ' (templat)'))
    return d


# ---------------------------------------------------------------------------
# Bukti: karantina & pemindaian (UC-04)
# ---------------------------------------------------------------------------
@router.post('/api/cases/{case_id}/evidence')
async def upload_evidence(case_id: str, background: BackgroundTasks, file: UploadFile = File(...), user=Depends(current_user)):
    m = _m()
    case = m._visible_case(case_id, user)
    if user['role'] == 'admin':
        raise HTTPException(400, 'Bukti diunggah oleh nasabah.')
    if case['status'] in ('Selesai', 'Ditolak'):
        raise HTTPException(409, 'Kasus sudah ditutup.')
    m._limit(f"upload:{user['session_token']}", RATE_UPLOAD_PER_MIN)
    data = await file.read(EVIDENCE_MAX_MB * 1024 * 1024 + 1)
    try:
        r = evidence.accept_upload(case_id=case_id, user_id=user['id'], filename=file.filename or 'bukti', data=data)
    except evidence.EvidenceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    background.add_task(evidence.process, r['id'])  # pemindaian asinkron setelah respons dikirim
    with connect() as c:
        c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                  (case_id, 'system', f"📎 Bukti \"{r['filename']}\" diterima dan sedang dipindai keamanannya (karantina).",
                   json.dumps({'kind': 'system', 'evidence_id': r['id']})))
        m._notify_admins(c, case_id, f"Bukti baru pada {case_id}: {r['filename']} (karantina).")
    audit(user['email'] or 'tamu', f"Mengunggah bukti #{r['id']} ke {case_id} (karantina)")
    return r


@router.get('/api/cases/{case_id}/evidence')
def list_evidence(case_id: str, user=Depends(current_user)):
    _m()._visible_case(case_id, user)
    cols = 'id,filename,size,status,created_at,scanned_at'
    if user['role'] == 'admin':
        cols += ',mime,scanner,scan_result,text_excerpt,reviewed_by'
    with connect() as c:
        return {'items': [dict(r) for r in c.execute(f'SELECT {cols} FROM evidence WHERE case_id=? ORDER BY id', (case_id,))]}


@router.get('/api/admin/evidence/{evidence_id}/download')
def download_evidence(evidence_id: int, user=Depends(admin_only)):
    try:
        ev, data = evidence.read_clean(evidence_id)
    except evidence.EvidenceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit(user['email'], f'Membuka bukti #{evidence_id} ({ev["filename"]})')
    return Response(data, media_type=ev['mime'], headers={'Content-Disposition': f'attachment; filename="{ev["filename"]}"',
                                                          'X-Content-Type-Options': 'nosniff'})


@router.post('/api/admin/evidence/{evidence_id}/review')
def review_evidence(evidence_id: int, user=Depends(admin_only)):
    with connect() as c:
        cur = c.execute("UPDATE evidence SET reviewed_by=? WHERE id=? AND status='Bersih'", (user['email'], evidence_id))
        if not cur.rowcount:
            raise HTTPException(409, 'Hanya bukti yang sudah bersih yang dapat ditandai ditinjau.')
    audit(user['email'], f'Meninjau bukti #{evidence_id}')
    return {'ok': True}


# ---------------------------------------------------------------------------
# Nasabah: tiket sendiri (data pribadi -> wajib AL2)
# ---------------------------------------------------------------------------
@router.get('/api/tickets')
def my_tickets(user=Depends(current_user)):
    _m()._require_level(user, 2)
    with connect() as c:
        rows = c.execute('SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC', (user['id'],)).fetchall()
    keep = ('id', 'case_id', 'category', 'status', 'priority', 'sla', 'created_at', 'resolved_at')
    return {'items': [{k: t[k] for k in keep} for t in map(crm.ticket_dict, rows)]}


# ---------------------------------------------------------------------------
# Kanal email (simulator email masuk)
# ---------------------------------------------------------------------------
@router.post('/api/channels/email/inbound')
def email_inbound(body: EmailInboundBody, user=Depends(supervisor_only)):
    """Pengirim email tidak terautentikasi -> diperlakukan AL0 (tamu) per alamat email; balasan = email keluar."""
    addr = body.from_email.strip().lower()
    if '@' not in addr:
        raise HTTPException(400, 'Alamat email pengirim tidak valid.')
    with connect() as c:
        g = c.execute('SELECT id FROM users WHERE email=?', (f'email:{addr}',)).fetchone()
        g_id = g['id'] if g else c.execute(
            "INSERT INTO users(email,name,role,password_hash,is_guest) VALUES(?,?,'customer',?,1)",
            (f'email:{addr}', f'Pengirim email {addr}', hash_password(secrets.token_urlsafe(16)))).lastrowid
        row = c.execute("""SELECT id FROM cases WHERE user_id=? AND channel='email' AND status NOT IN ('Selesai','Ditolak')
                           ORDER BY updated_at DESC LIMIT 1""", (g_id,)).fetchone()
    guest = {'id': g_id, 'email': f'email:{addr}', 'name': f'Pengirim email {addr}', 'role': 'customer', 'is_guest': 1,
             'auth_level': 0, 'channel': 'email', 'session_token': f'email:{addr}'}
    text = f"{body.subject}\n{body.body}".strip() if body.subject else body.body
    r = _m().process_message(guest, text, row['id'] if row else None, channel='email')
    audit(user['email'], f"Simulasi email masuk dari {addr} -> {r['case_id']}")
    return {'case_id': r['case_id'], 'status': r['status'], 'intent_code': r['intent_code'], 'ticket_id': r['ticket_id'],
            'outbound_email': {'to': addr, 'subject': f"Re: {body.subject or 'Pertanyaan Anda'} [{r['case_id']}]", 'body': r['reply']}}


# ---------------------------------------------------------------------------
# Supervisor: analitik SLA & akar masalah, sampling QA (UC-07)
# ---------------------------------------------------------------------------
@router.get('/api/admin/analytics')
def analytics(user=Depends(supervisor_only)):
    with connect() as c:
        return crm.analytics(c)


@router.get('/api/admin/qa/sample')
def qa_sample(n: int = 5, user=Depends(supervisor_only)):
    with connect() as c:
        return {'items': crm.qa_sample(c, max(1, min(n, 20))), 'criteria': crm.QA_CRITERIA}


@router.post('/api/admin/qa/{case_id}')
def qa_submit(case_id: str, body: QABody, user=Depends(supervisor_only)):
    _m()._visible_case(case_id, user)
    r = _crm_call(crm.qa_submit, case_id, user['email'], body.scores, body.notes)
    audit(user['email'], f'Penilaian QA {case_id}: {r["total"]}')
    return r


@router.get('/api/admin/qa')
def qa_list(user=Depends(supervisor_only)):
    with connect() as c:
        rows = [dict(r) | {'scores': json.loads(r['scores'])} for r in c.execute('SELECT * FROM qa_reviews ORDER BY id DESC LIMIT 100')]
    return {'items': rows}


