import json
import logging
import re

from typing import Optional
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .security import hash_password

from . import agent, knowledge, llm
from .config import (ASSISTANT_NAME, GEMINI_MODEL, GUARDRAIL_MAX_STRIKES, KNOWLEDGE_AUTO_SYNC, KNOWLEDGE_DIR,
                     LLM_CLASSIFIER, MAX_UPLOAD_MB, ORG_NAME)
from .db import connect, init_db
from .guardrails import describe
from .security import admin_only, audit, create_token, current_user, verify_password

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger('vteki')
app = FastAPI(title='V-TEKI Workspace API', version='2.0.0',
              description='Backend layanan pelanggan AI: Gemini + LangGraph + knowledge base + guardrail')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:8000', 'http://localhost:8000', 'http://127.0.0.1:5500', 'http://localhost:5500'],
                   allow_credentials=False, allow_methods=['*'], allow_headers=['*'])


@app.on_event('startup')
def startup():
    init_db()
    if KNOWLEDGE_AUTO_SYNC:
        for r in knowledge.sync_knowledge_dir(KNOWLEDGE_DIR):
            if r.get('action') in ('created', 'replaced'):
                log.info('Knowledge %s: %s (%s bagian, versi %s)', r['action'], r['filename'], r['chunks'], r['version'])
                audit('system', f"Sinkron knowledge/{r['filename']}: {r['action']} v{r['version']} ({r['chunks']} bagian)")
            elif r.get('action') == 'error':
                log.warning('Knowledge gagal diimpor: %s: %s', r['filename'], r.get('error'))


# ---------------------------------------------------------------------------
# Model request
# ---------------------------------------------------------------------------

class RegisterBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=120)
    password: str = Field(min_length=8, max_length=128)

class LoginBody(BaseModel):
    email: str
    password: str


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    case_id: Optional[str] = None


class CaseBody(BaseModel):
    subject: str = 'Percakapan baru'


class KnowledgeBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = 'FAQ'
    keywords: str = ''
    body: str = Field(min_length=1, max_length=20000)
    status: str = 'Aktif'
    kind: str = 'fakta'
    source_url: str = ''
    validity: str = 'umum'


class DocumentPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = None
    source_url: Optional[str] = None
    checked_at: Optional[str] = None


class ImportTextBody(BaseModel):
    title: str = 'Dokumen impor'
    body: str = Field(min_length=1, max_length=500_000)
    status: str = 'Aktif'


class SearchTestBody(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class DecisionBody(BaseModel):
    approve: bool


class ReviewBody(BaseModel):
    verdict: str = 'confirmed'  # confirmed | false_positive


class AgentReplyBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
STATUS_RANK = {'Baru': 0, 'Selesai otomatis': 0, 'Ditangani': 1, 'Menunggu agen': 2, 'Menunggu persetujuan': 3, 'Dialihkan ke admin': 4}
PRIORITY_RANK = {'Normal': 0, 'Tinggi': 1}


def _merge_status(current: str, new: Optional[str]) -> str:
    """Status menunggu tidak boleh 'turun' hanya karena customer bertanya hal lain."""
    if not new:
        return current
    return new if STATUS_RANK.get(new, 0) >= STATUS_RANK.get(current, 0) else current


def _loads(s):
    try:
        return json.loads(s or '{}')
    except (TypeError, ValueError):
        return {}


def _greeting():
    return (f'Halo, saya {ASSISTANT_NAME} untuk informasi umum layanan {ORG_NAME}. '
            f'Saya bukan kanal resmi {ORG_NAME} dan tidak dapat mengakses rekening Anda. Ada yang bisa saya bantu?')


def _notify_admins(c, case_id, message):
    for a in c.execute("SELECT id FROM users WHERE role='admin'").fetchall():
        c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (a['id'], case_id, message[:500]))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.get('/api/health')
def health():
    return {'status': 'ok', 'model': GEMINI_MODEL, 'gemini_configured': llm.is_configured(), 'router': 'langgraph',
            'llm_classifier': LLM_CLASSIFIER, 'org': ORG_NAME, 'knowledge': knowledge.kb_stats()}


@app.post('/api/auth/login')
def login(body: LoginBody):
    with connect() as c:
        row = c.execute('SELECT * FROM users WHERE email=?', (body.email.strip().lower(),)).fetchone()
        if not row or not verify_password(body.password, row['password_hash']):
            raise HTTPException(401, 'Email atau kata sandi salah.')
        token = create_token()
        c.execute('INSERT INTO sessions(token,user_id) VALUES(?,?)', (token, row['id']))
    audit(row['email'], 'Login berhasil')
    return {'access_token': token, 'token_type': 'bearer',
            'user': {'id': row['id'], 'email': row['email'], 'name': row['name'], 'role': row['role'],
                     'guardrail_strikes': row['guardrail_strikes'], 'ai_suspended': row['ai_suspended']}}


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return {'user': user, 'max_strikes': GUARDRAIL_MAX_STRIKES, 'org': ORG_NAME}


@app.post('/api/auth/logout')
def logout(user=Depends(current_user), authorization: str | None = Header(default=None)):
    token = (authorization or '').replace('Bearer ', '', 1).strip()
    with connect() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (token,))
    audit(user['email'], 'Logout')
    return {'ok': True}


# ---------------------------------------------------------------------------
# Kasus & pesan
# ---------------------------------------------------------------------------
def _visible_case(case_id, user):
    with connect() as c:
        row = c.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Kasus tidak ditemukan.')
    if user['role'] != 'admin' and row['user_id'] != user['id']:
        raise HTTPException(403, 'Anda tidak memiliki akses ke kasus ini.')
    return dict(row)


def _new_case(user, subject='Percakapan baru'):
    with connect() as c:
        latest = c.execute("SELECT MAX(CAST(SUBSTR(id,5) AS INTEGER)) FROM cases WHERE id LIKE 'CAS-%'").fetchone()[0] or 1000
        case_id = f'CAS-{latest + 1}'
        c.execute('INSERT INTO cases(id,user_id,subject) VALUES(?,?,?)', (case_id, user['id'], subject[:200]))
        c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                  (case_id, 'assistant', _greeting(), json.dumps({'kind': 'template'})))
    return case_id


@app.get('/api/cases')
def list_cases(user=Depends(current_user)):
    with connect() as c:
        if user['role'] == 'admin':
            rows = c.execute('''SELECT cases.*,users.name AS customer_name,users.email AS customer_email
                                FROM cases JOIN users ON users.id=cases.user_id ORDER BY cases.updated_at DESC''').fetchall()
        else:
            rows = c.execute('SELECT * FROM cases WHERE user_id=? ORDER BY updated_at DESC', (user['id'],)).fetchall()
    return {'items': [dict(r) for r in rows]}


@app.post('/api/cases')
def create_case(body: CaseBody, user=Depends(current_user)):
    case_id = _new_case(user, body.subject)
    audit(user['email'], f'Membuat kasus {case_id}')
    return {'case_id': case_id, 'messages': get_messages(case_id, user)['items']}


def get_messages(case_id: str, user):
    _visible_case(case_id, user)
    with connect() as c:
        rows = c.execute('SELECT id,sender,content,created_at,meta FROM messages WHERE case_id=? ORDER BY id', (case_id,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        meta = _loads(d.pop('meta'))
        if user['role'] != 'admin':  # detail internal hanya untuk admin
            meta = {k: v for k, v in meta.items() if k in ('kind', 'sources', 'masked')}
        d['meta'] = meta
        items.append(d)
    return {'items': items}


@app.get('/api/cases/{case_id}/messages')
def messages(case_id: str, user=Depends(current_user)):
    return get_messages(case_id, user)


# ---------------------------------------------------------------------------
# Chat (LangGraph)
# ---------------------------------------------------------------------------

@app.post('/api/auth/register')
def register(body: RegisterBody):
    email = body.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise HTTPException(400, 'Format email tidak valid.')
    if not (re.search(r'[A-Za-z]', body.password) and re.search(r'\d', body.password)):
        raise HTTPException(400, 'Kata sandi harus mengandung huruf dan angka.')
    with connect() as c:
        if c.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            raise HTTPException(409, 'Email sudah terdaftar.')
        # role dipaksa 'customer': jangan pernah menerima role dari input pengguna
        c.execute('INSERT INTO users(email,name,role,password_hash) VALUES(?,?,?,?)',
                  (email, body.name.strip(), 'customer', hash_password(body.password)))
    audit(email, 'Registrasi akun baru')
    return login(LoginBody(email=email, password=body.password)) 


@app.post('/api/chat')
def chat(body: ChatBody, user=Depends(current_user)):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, 'Pesan tidak boleh kosong.')
    if user['role'] == 'admin':
        raise HTTPException(400, 'Admin membalas lewat menu Kasus & Percakapan.')
    case_id = body.case_id
    case = _visible_case(case_id, user) if case_id else _visible_case(_new_case(user, message[:80]), user)
    case_id = case['id']
    if case['status'] in ('Selesai', 'Ditolak'):
        raise HTTPException(409, 'Kasus ini sudah ditutup. Buat percakapan baru untuk melanjutkan.')

    with connect() as c:
        fresh = dict(c.execute('SELECT id,email,name,role,guardrail_strikes,ai_suspended FROM users WHERE id=?', (user['id'],)).fetchone())
        rows = c.execute('SELECT sender,content,meta FROM messages WHERE case_id=? ORDER BY id', (case_id,)).fetchall()
    # Pesan yang ditandai manipulasi & pesan sistem tidak dimasukkan ke riwayat LLM.
    history = [{'sender': r['sender'], 'content': r['content']} for r in rows
               if r['sender'] != 'system' and not _loads(r['meta']).get('flagged')]

    state = agent.run_chat(user=fresh, case=case, message=message, history=history)

    esc = state.get('escalation')
    sensitive = state.get('sensitive') or []
    stored_message = state.get('message') or message
    reply = state.get('reply') or ''
    kind = state.get('reply_kind') or 'ai'
    hits = {h['id']: h for h in state.get('hits') or []}
    sources = [{'id': i, 'title': hits[i]['title'], 'source_url': hits[i].get('source_url') or '',
                'document_title': hits[i].get('document_title') or ''} for i in state.get('source_ids') or [] if i in hits]
    status = _merge_status(case['status'], state.get('status'))
    priority = state.get('priority') or case['priority']
    if PRIORITY_RANK.get(case['priority'], 0) > PRIORITY_RANK.get(priority, 0):
        priority = case['priority']
    risk = max(int(case['risk'] or 0), int(state.get('risk') or 0))
    intent = state.get('case_intent') or case['intent']
    if case['intent'] == agent.INTENT_LABEL['manipulation']:
        intent = case['intent']
    trace = state.get('trace') or []

    with connect() as c:
        c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                  (case_id, 'customer', stored_message, json.dumps({'flagged': bool(esc), 'masked': sensitive}, ensure_ascii=False)))
        c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                  (case_id, 'system' if kind in ('warning', 'system') else 'assistant', reply,
                   json.dumps({'kind': kind, 'sources': sources, 'route': trace, 'intent': state.get('intent'),
                               'classifier': state.get('classifier'), 'handoff': state.get('handoff_reason', ''),
                               'retrieved': [{'id': h['id'], 'title': h['title'], 'score': h['score']} for h in hits.values()],
                               'output_problems': state.get('output_problems') or []}, ensure_ascii=False)))
        subject = case['subject'] if case['subject'] not in ('Percakapan baru', '') else stored_message[:120]
        c.execute('''UPDATE cases SET subject=?,intent=?,status=?,priority=?,risk=?,handoff_reason=?,
                     flagged=MAX(flagged,?),ai_locked=MAX(ai_locked,?),updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (subject, intent, status, priority, risk, state.get('handoff_reason') or case.get('handoff_reason') or '',
                   1 if esc else 0, 1 if esc else 0, case_id))

        if esc:
            c.execute('''INSERT INTO guardrail_events(case_id,user_id,stage,score,reasons,message,action)
                         VALUES(?,?,?,?,?,?,?)''',
                      (case_id, fresh['id'], esc['stage'], esc['score'], json.dumps(esc['reasons']), stored_message[:2000],
                       'Dialihkan ke admin' + ('; AI dinonaktifkan untuk customer' if esc['suspended'] else '')))
            c.execute('UPDATE users SET guardrail_strikes=?, ai_suspended=MAX(ai_suspended,?) WHERE id=?',
                      (esc['strikes'], 1 if esc['suspended'] else 0, fresh['id']))
            _notify_admins(c, case_id, f"⚠️ Upaya manipulasi AI oleh {fresh['name']} ({fresh['email']}) pada {case_id}: "
                                       f"{', '.join(esc['reason_labels'])}. Peringatan {esc['strikes']}/{GUARDRAIL_MAX_STRIKES}"
                                       + ('; akses AI customer dinonaktifkan.' if esc['suspended'] else '.'))
            c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)',
                      (fresh['id'], case_id, f'Peringatan keamanan {esc["strikes"]}/{GUARDRAIL_MAX_STRIKES}: percakapan {case_id} dialihkan ke admin.'))
        if sensitive:
            c.execute('''INSERT INTO guardrail_events(case_id,user_id,stage,score,reasons,message,action,reviewed,verdict)
                         VALUES(?,?,?,?,?,?,?,1,'info')''',
                      (case_id, fresh['id'], 'sensitive', 0, json.dumps(sensitive), stored_message[:2000], 'Data rahasia disamarkan'))
        if state.get('output_problems'):
            c.execute('''INSERT INTO guardrail_events(case_id,user_id,stage,score,reasons,message,action)
                         VALUES(?,?,?,?,?,?,?)''',
                      (case_id, fresh['id'], 'output', len(state['output_problems']), json.dumps(state['output_problems']),
                       (state.get('answer') or '')[:2000], 'Jawaban AI diblok & diteruskan ke agen'))
        if status != case['status'] and status in ('Menunggu agen', 'Menunggu persetujuan', 'Dialihkan ke admin'):
            note = {'Menunggu persetujuan': 'Perlu tinjauan agen/supervisor.', 'Dialihkan ke admin': 'Sedang ditinjau admin.'}.get(status, 'Agen customer service akan menindaklanjuti.')
            if not esc:
                c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (fresh['id'], case_id, f'Tiket {case_id}: {status}. {note}'))
                if status == 'Menunggu persetujuan' or risk:
                    _notify_admins(c, case_id, f'Tiket {case_id} ({intent}) dari {fresh["name"]}: {status}.')

    audit(user['email'], f"Chat {case_id}; intent={intent}; status={status}; rute={' > '.join(t.split(':')[0] for t in trace)}"
                         + (f"; GUARDRAIL={','.join(esc['reasons'])}" if esc else ''))
    return {
        'case_id': case_id, 'reply': reply, 'reply_kind': kind, 'status': status, 'intent': intent, 'risk': bool(risk),
        'sources': sources, 'warning': bool(esc), 'masked': sensitive,
        'strikes': esc['strikes'] if esc else fresh['guardrail_strikes'],
        'ai_suspended': bool(esc and esc['suspended']) or bool(fresh['ai_suspended']), 'max_strikes': GUARDRAIL_MAX_STRIKES,
    }


# ---------------------------------------------------------------------------
# Knowledge: dokumen
# ---------------------------------------------------------------------------
async def _read_upload(f: UploadFile) -> bytes:
    data = await f.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise knowledge.KnowledgeFileError(f'Ukuran file melebihi {MAX_UPLOAD_MB} MB.')
    if not data:
        raise knowledge.KnowledgeFileError('File kosong.')
    return data


@app.post('/api/knowledge/upload')
async def upload_knowledge(files: list[UploadFile] = File(...), status: str = Form('Aktif'), mode: str = Form('auto'),
                           category: str = Form(''), user=Depends(admin_only)):
    if mode not in ('auto', 'new') and not mode.startswith('replace:'):
        raise HTTPException(400, 'Mode harus auto, new, atau replace:<id>.')
    if mode.startswith('replace:') and len(files) != 1:
        raise HTTPException(400, 'Mode ganti versi hanya untuk satu file.')
    results = []
    for f in files:
        name = (f.filename or 'dokumen').replace('\\', '/').split('/')[-1][:200]
        try:
            data = await _read_upload(f)
            r = knowledge.ingest_document(filename=name, data=data, status=status, mode=mode, category=category.strip()[:60],
                                          uploaded_by=user['email'])
            audit(user['email'], f"Upload knowledge {name}: {r['action']} KD-{r['document_id']} v{r['version']} ({r['chunks']} bagian)")
        except knowledge.KnowledgeFileError as exc:
            r = {'filename': name, 'action': 'error', 'error': str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception('Upload gagal')
            r = {'filename': name, 'action': 'error', 'error': f'Gagal memproses file: {type(exc).__name__}'}
        results.append(r)
    return {'results': results}


@app.post('/api/knowledge/preview')
async def preview_knowledge(file: UploadFile = File(...), user=Depends(admin_only)):
    try:
        data = await _read_upload(file)
        text, ftype = knowledge.extract_text(file.filename or 'dokumen.txt', data)
    except knowledge.KnowledgeFileError as exc:
        raise HTTPException(400, str(exc))
    parsed = knowledge.chunk_document(text, default_title=(file.filename or 'Dokumen').rsplit('.', 1)[0])
    with connect() as c:
        same = c.execute('SELECT id,title,version FROM knowledge_documents WHERE filename=? ORDER BY id DESC LIMIT 1', (file.filename,)).fetchone()
    return {'title': parsed['title'], 'file_type': ftype, 'source_url': parsed['source_url'], 'checked_at': parsed['checked_at'],
            'existing': dict(same) if same else None,
            'chunks': [{k: ch[k] for k in ('title', 'kind', 'category', 'status', 'validity', 'source_url', 'keywords')} | {'chars': len(ch['body']), 'preview': ch['body'][:220]}
                       for ch in parsed['chunks']]}


@app.post('/api/knowledge/import-text')
def import_text(body: ImportTextBody, user=Depends(admin_only)):
    try:
        r = knowledge.ingest_document(filename=f"{body.title.strip() or 'Dokumen impor'}.md", text=body.body, title=body.title.strip() or None,
                                      status=body.status, mode='auto', uploaded_by=user['email'])
    except knowledge.KnowledgeFileError as exc:
        raise HTTPException(400, str(exc))
    audit(user['email'], f"Impor teks KD-{r['document_id']}: {r['action']} ({r['chunks']} bagian)")
    return r


@app.get('/api/knowledge/documents')
def list_documents(user=Depends(admin_only)):
    with connect() as c:
        rows = c.execute('''SELECT d.id,d.title,d.filename,d.file_type,d.version,d.status,d.chunk_count,d.source_url,d.checked_at,
                                   d.uploaded_by,d.created_at,d.updated_at,
                                   (SELECT COUNT(*) FROM knowledge k WHERE k.document_id=d.id AND k.status='Aktif' AND k.kind='fakta') active_facts,
                                   (SELECT COUNT(*) FROM knowledge k WHERE k.document_id=d.id AND k.kind='instruksi' AND k.status='Aktif') active_instructions
                            FROM knowledge_documents d ORDER BY d.updated_at DESC''').fetchall()
        legacy = c.execute('SELECT COUNT(*) FROM knowledge WHERE document_id IS NULL').fetchone()[0]
    return {'items': [dict(r) for r in rows], 'manual_articles': legacy}


@app.get('/api/knowledge/documents/{doc_id}')
def get_document(doc_id: int, user=Depends(admin_only)):
    with connect() as c:
        d = c.execute('SELECT * FROM knowledge_documents WHERE id=?', (doc_id,)).fetchone()
        if not d:
            raise HTTPException(404, 'Dokumen tidak ditemukan.')
        chunks = c.execute('SELECT * FROM knowledge WHERE document_id=? ORDER BY position,id', (doc_id,)).fetchall()
    doc = dict(d)
    doc.pop('raw_text', None)
    return {'document': doc, 'chunks': [dict(r) for r in chunks]}


@app.get('/api/knowledge/documents/{doc_id}/download', response_class=PlainTextResponse)
def download_document(doc_id: int, user=Depends(admin_only)):
    with connect() as c:
        d = c.execute('SELECT filename,raw_text FROM knowledge_documents WHERE id=?', (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, 'Dokumen tidak ditemukan.')
    name = d['filename'].rsplit('.', 1)[0] + '.md'
    return PlainTextResponse(d['raw_text'], media_type='text/markdown; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename="{name}"'})


@app.patch('/api/knowledge/documents/{doc_id}')
def patch_document(doc_id: int, body: DocumentPatch, user=Depends(admin_only)):
    fields, values = [], []
    if body.status is not None:
        if body.status not in ('Aktif', 'Nonaktif'):
            raise HTTPException(400, 'Status dokumen harus Aktif atau Nonaktif.')
        fields.append('status=?'); values.append(body.status)
    for name in ('title', 'source_url', 'checked_at'):
        v = getattr(body, name)
        if v is not None:
            fields.append(f'{name}=?'); values.append(v.strip())
    if not fields:
        raise HTTPException(400, 'Tidak ada perubahan.')
    with connect() as c:
        cur = c.execute(f"UPDATE knowledge_documents SET {','.join(fields)},updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values, doc_id))
        if not cur.rowcount:
            raise HTTPException(404, 'Dokumen tidak ditemukan.')
    knowledge.invalidate_index()
    audit(user['email'], f'Mengubah dokumen KD-{doc_id}: {", ".join(f.split("=")[0] for f in fields)}')
    return get_document(doc_id, user)


@app.post('/api/knowledge/documents/{doc_id}/reindex')
def reindex_document(doc_id: int, user=Depends(admin_only)):
    """Pecah ulang dokumen dari teks aslinya (perubahan manual per bagian akan tertimpa)."""
    with connect() as c:
        d = c.execute('SELECT * FROM knowledge_documents WHERE id=?', (doc_id,)).fetchone()
        if not d:
            raise HTTPException(404, 'Dokumen tidak ditemukan.')
        c.execute("UPDATE knowledge_documents SET sha256='' WHERE id=?", (doc_id,))
    r = knowledge.ingest_document(filename=d['filename'], text=d['raw_text'], title=d['title'], status=d['status'] if d['status'] == 'Aktif' else 'Draf',
                                  mode=f'replace:{doc_id}', uploaded_by=user['email'])
    audit(user['email'], f'Indeks ulang KD-{doc_id} ({r["chunks"]} bagian)')
    return r


@app.delete('/api/knowledge/documents/{doc_id}')
def delete_document(doc_id: int, user=Depends(admin_only)):
    with connect() as c:
        c.execute('DELETE FROM knowledge WHERE document_id=?', (doc_id,))
        cur = c.execute('DELETE FROM knowledge_documents WHERE id=?', (doc_id,))
        if not cur.rowcount:
            raise HTTPException(404, 'Dokumen tidak ditemukan.')
    knowledge.invalidate_index()
    audit(user['email'], f'Menghapus dokumen KD-{doc_id} beserta seluruh bagiannya')
    return {'ok': True}


# ---------------------------------------------------------------------------
# Knowledge: bagian / artikel
# ---------------------------------------------------------------------------
def _validate_article(body: KnowledgeBody):
    if body.status not in ('Aktif', 'Draf'):
        raise HTTPException(400, 'Status harus Aktif atau Draf.')
    if body.kind not in ('fakta', 'instruksi', 'metadata'):
        raise HTTPException(400, 'Jenis harus fakta, instruksi, atau metadata.')
    if body.validity not in ('umum', 'dinamis'):
        raise HTTPException(400, 'Validitas harus umum atau dinamis.')


@app.get('/api/knowledge')
def list_knowledge(q: str = '', document_id: Optional[int] = None, user=Depends(admin_only)):
    sql = '''SELECT k.*, d.title AS document_title FROM knowledge k LEFT JOIN knowledge_documents d ON d.id=k.document_id
             WHERE (k.title LIKE ? OR k.body LIKE ? OR k.keywords LIKE ?)'''
    args = [f'%{q}%'] * 3
    if document_id is not None:
        sql += ' AND k.document_id=?'
        args.append(document_id)
    sql += ' ORDER BY k.document_id IS NULL, k.document_id, k.position, k.updated_at DESC'
    with connect() as c:
        rows = c.execute(sql, args).fetchall()
    return {'items': [dict(r) for r in rows]}


@app.post('/api/knowledge')
def create_knowledge(body: KnowledgeBody, user=Depends(admin_only)):
    _validate_article(body)
    with connect() as c:
        cur = c.execute('''INSERT INTO knowledge(title,category,keywords,body,status,kind,source_url,validity,checked_at)
                           VALUES(?,?,?,?,?,?,?,?,DATE('now'))''',
                        (body.title, body.category, body.keywords, body.body, body.status, body.kind, body.source_url, body.validity))
        item = c.execute('SELECT * FROM knowledge WHERE id=?', (cur.lastrowid,)).fetchone()
    knowledge.invalidate_index()
    audit(user['email'], f'Menambah artikel KB-{item["id"]}')
    return dict(item)


@app.put('/api/knowledge/{item_id}')
def update_knowledge(item_id: int, body: KnowledgeBody, user=Depends(admin_only)):
    _validate_article(body)
    with connect() as c:
        cur = c.execute('''UPDATE knowledge SET title=?,category=?,keywords=?,body=?,status=?,kind=?,source_url=?,validity=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                        (body.title, body.category, body.keywords, body.body, body.status, body.kind, body.source_url, body.validity, item_id))
        if not cur.rowcount:
            raise HTTPException(404, 'Artikel tidak ditemukan.')
        item = c.execute('SELECT * FROM knowledge WHERE id=?', (item_id,)).fetchone()
    knowledge.invalidate_index()
    audit(user['email'], f'Mengubah artikel KB-{item_id}')
    return dict(item)


@app.delete('/api/knowledge/{item_id}')
def delete_knowledge(item_id: int, user=Depends(admin_only)):
    with connect() as c:
        row = c.execute('SELECT document_id FROM knowledge WHERE id=?', (item_id,)).fetchone()
        cur = c.execute('DELETE FROM knowledge WHERE id=?', (item_id,))
        if not cur.rowcount:
            raise HTTPException(404, 'Artikel tidak ditemukan.')
        if row and row['document_id']:
            c.execute('''UPDATE knowledge_documents SET chunk_count=(SELECT COUNT(*) FROM knowledge WHERE document_id=?),
                         updated_at=CURRENT_TIMESTAMP WHERE id=?''', (row['document_id'], row['document_id']))
    knowledge.invalidate_index()
    audit(user['email'], f'Menghapus artikel KB-{item_id}')
    return {'ok': True}


@app.post('/api/knowledge/search-test')
def search_test(body: SearchTestBody, user=Depends(admin_only)):
    """Uji: bagian knowledge mana yang akan dipakai AI untuk pertanyaan ini."""
    hits = knowledge.search(body.query, debug=True)
    intent = agent.rule_intent(body.query)
    from .guardrails import detect_manipulation
    g = detect_manipulation(body.query)
    return {'query': body.query, 'intent_rule': intent, 'guardrail': {'flagged': g.flagged, 'score': g.score, 'reasons': describe(g.reasons)},
            'hits': [{k: h[k] for k in ('id', 'title', 'category', 'score', 'matched', 'used', 'source_url', 'document_title')} | {'preview': h['body'][:260]} for h in hits]}


# ---------------------------------------------------------------------------
# Notifikasi
# ---------------------------------------------------------------------------
@app.get('/api/notifications')
def notifications(user=Depends(current_user)):
    with connect() as c:
        rows = c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200', (user['id'],)).fetchall()
    return {'items': [dict(r) for r in rows]}


@app.post('/api/notifications/read-all')
def read_all(user=Depends(current_user)):
    with connect() as c:
        c.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (user['id'],))
    return {'ok': True}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.get('/api/admin/dashboard')
def dashboard(user=Depends(admin_only)):
    with connect() as c:
        total = c.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
        statuses = {r['status']: r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM cases GROUP BY status')}
        audits = [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 10').fetchall()]
        guard_pending = c.execute("SELECT COUNT(*) FROM guardrail_events WHERE reviewed=0").fetchone()[0]
        suspended = c.execute("SELECT COUNT(*) FROM users WHERE ai_suspended=1").fetchone()[0]
        unread = c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0', (user['id'],)).fetchone()[0]
    kb = knowledge.kb_stats()
    return {'total_cases': total, 'statuses': statuses, 'active_knowledge_articles': kb['active_chunks'], 'knowledge_documents': kb['documents'],
            'guardrail_pending': guard_pending, 'suspended_users': suspended, 'unread_notifications': unread,
            'recent_audit': audits, 'model': GEMINI_MODEL, 'gemini_configured': llm.is_configured(),
            'llm_classifier': LLM_CLASSIFIER, 'router': 'LangGraph', 'org': ORG_NAME, 'max_strikes': GUARDRAIL_MAX_STRIKES}


@app.get('/api/admin/graph')
def graph(user=Depends(admin_only)):
    return {'mermaid': agent.graph_mermaid()}


@app.get('/api/admin/approvals')
def approvals(user=Depends(admin_only)):
    with connect() as c:
        rows = c.execute('''SELECT cases.*,users.name customer_name,users.email customer_email,
          (SELECT content FROM messages WHERE messages.case_id=cases.id AND sender='customer' ORDER BY id DESC LIMIT 1) last_message
          FROM cases JOIN users ON users.id=cases.user_id WHERE cases.status='Menunggu persetujuan' ORDER BY cases.updated_at DESC''').fetchall()
    return {'items': [dict(r) for r in rows]}


@app.post('/api/admin/approvals/{case_id}')
def decide(case_id: str, body: DecisionBody, user=Depends(admin_only)):
    case = _visible_case(case_id, user)
    if case['status'] != 'Menunggu persetujuan':
        raise HTTPException(409, 'Kasus tidak sedang menunggu persetujuan.')
    status = 'Selesai' if body.approve else 'Ditolak'
    answer = ('Permintaan disetujui untuk ditindaklanjuti. Catatan demo: persetujuan ini tidak terhubung ke sistem transaksi bank.'
              if body.approve else 'Permintaan belum disetujui. Agen customer service akan menindaklanjuti.')
    with connect() as c:
        c.execute('UPDATE cases SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, case_id))
        c.execute('INSERT INTO messages(case_id,sender,content) VALUES(?,?,?)', (case_id, 'agent', answer))
        c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (case['user_id'], case_id, f'Tiket {case_id}: {status}. {answer}'))
    audit(user['email'], f'{"Menyetujui" if body.approve else "Menolak"} {case_id}')
    return {'ok': True, 'case_id': case_id, 'status': status}


@app.get('/api/admin/audit')
def audit_log(user=Depends(admin_only)):
    with connect() as c:
        rows = c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200').fetchall()
    return {'items': [dict(r) for r in rows]}


@app.post('/api/admin/cases/{case_id}/reply')
def agent_reply(case_id: str, body: AgentReplyBody, user=Depends(admin_only)):
    case = _visible_case(case_id, user)
    if case['status'] in ('Selesai', 'Ditolak'):
        raise HTTPException(409, 'Kasus sudah ditutup.')
    message = body.message.strip()
    with connect() as c:
        c.execute('INSERT INTO messages(case_id,sender,content) VALUES(?,?,?)', (case_id, 'agent', message))
        c.execute("UPDATE cases SET status='Ditangani',updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (case['user_id'], case_id, f'Agen CS membalas tiket {case_id}.'))
    audit(user['email'], f'Agen membalas {case_id}')
    return {'ok': True, 'case_id': case_id, 'status': 'Ditangani'}


@app.post('/api/admin/cases/{case_id}/resolve')
def resolve_case(case_id: str, user=Depends(admin_only)):
    case = _visible_case(case_id, user)
    with connect() as c:
        c.execute("UPDATE cases SET status='Selesai',updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        c.execute('INSERT INTO messages(case_id,sender,content) VALUES(?,?,?)', (case_id, 'agent', 'Kasus ini telah ditandai selesai oleh agen customer service.'))
        c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (case['user_id'], case_id, f'Tiket {case_id} telah diselesaikan.'))
    audit(user['email'], f'Menyelesaikan {case_id}')
    return {'ok': True, 'case_id': case_id, 'status': 'Selesai'}


@app.post('/api/admin/cases/{case_id}/unlock-ai')
def unlock_case_ai(case_id: str, user=Depends(admin_only)):
    case = _visible_case(case_id, user)
    with connect() as c:
        c.execute("UPDATE cases SET ai_locked=0,status=CASE WHEN status='Dialihkan ke admin' THEN 'Ditangani' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                  (case_id, 'system', 'Admin telah mengaktifkan kembali asisten AI untuk percakapan ini.', json.dumps({'kind': 'system'})))
    audit(user['email'], f'Membuka kunci AI pada {case_id}')
    return {'ok': True, 'case_id': case_id, 'user_id': case['user_id']}


# --- Guardrail -----------------------------------------------------------------
@app.get('/api/admin/guardrail')
def guardrail_events(only_pending: bool = False, user=Depends(admin_only)):
    sql = '''SELECT g.*, u.name customer_name, u.email customer_email, u.guardrail_strikes, u.ai_suspended,
                    c.status case_status, c.ai_locked
             FROM guardrail_events g LEFT JOIN users u ON u.id=g.user_id LEFT JOIN cases c ON c.id=g.case_id'''
    if only_pending:
        sql += ' WHERE g.reviewed=0'
    sql += ' ORDER BY g.id DESC LIMIT 300'
    with connect() as c:
        rows = [dict(r) for r in c.execute(sql).fetchall()]
        users = [dict(r) for r in c.execute('''SELECT id,name,email,guardrail_strikes,ai_suspended FROM users
                                               WHERE role='customer' AND (guardrail_strikes>0 OR ai_suspended=1) ORDER BY ai_suspended DESC, guardrail_strikes DESC''').fetchall()]
    for r in rows:
        r['reasons'] = _loads(r['reasons']) if r['reasons'].startswith('[') else [r['reasons']]
        r['reason_labels'] = describe(r['reasons']) if r['stage'] in ('input', 'llm') else r['reasons']
    return {'items': rows, 'users': users, 'max_strikes': GUARDRAIL_MAX_STRIKES}


@app.post('/api/admin/guardrail/{event_id}/review')
def review_event(event_id: int, body: ReviewBody, user=Depends(admin_only)):
    if body.verdict not in ('confirmed', 'false_positive'):
        raise HTTPException(400, 'Verdict harus confirmed atau false_positive.')
    with connect() as c:
        ev = c.execute('SELECT * FROM guardrail_events WHERE id=?', (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, 'Kejadian tidak ditemukan.')
        c.execute('UPDATE guardrail_events SET reviewed=1,reviewed_by=?,verdict=? WHERE id=?', (user['email'], body.verdict, event_id))
        if body.verdict == 'false_positive' and ev['stage'] in ('input', 'llm') and ev['verdict'] != 'false_positive':
            # Salah deteksi: kurangi peringatan, buka kunci AI kasus & akun bila di bawah batas
            c.execute('UPDATE users SET guardrail_strikes=MAX(guardrail_strikes-1,0) WHERE id=?', (ev['user_id'],))
            c.execute(f'UPDATE users SET ai_suspended=0 WHERE id=? AND guardrail_strikes<{GUARDRAIL_MAX_STRIKES}', (ev['user_id'],))
            c.execute("UPDATE cases SET ai_locked=0,status=CASE WHEN status='Dialihkan ke admin' THEN 'Ditangani' ELSE status END WHERE id=?", (ev['case_id'],))
            c.execute('INSERT INTO messages(case_id,sender,content,meta) VALUES(?,?,?,?)',
                      (ev['case_id'], 'system', 'Admin meninjau ulang pesan Anda dan mencabut peringatan sebelumnya. Asisten AI aktif kembali.', json.dumps({'kind': 'system'})))
            c.execute('INSERT INTO notifications(user_id,case_id,message) VALUES(?,?,?)', (ev['user_id'], ev['case_id'], 'Peringatan keamanan Anda telah dicabut oleh admin.'))
    audit(user['email'], f'Meninjau guardrail #{event_id}: {body.verdict}')
    return {'ok': True}


@app.post('/api/admin/users/{user_id}/restore-ai')
def restore_ai(user_id: int, user=Depends(admin_only)):
    with connect() as c:
        cur = c.execute("UPDATE users SET guardrail_strikes=0, ai_suspended=0 WHERE id=? AND role='customer'", (user_id,))
        if not cur.rowcount:
            raise HTTPException(404, 'Customer tidak ditemukan.')
        c.execute('INSERT INTO notifications(user_id,message) VALUES(?,?)', (user_id, 'Akses asisten AI pada akun Anda telah dipulihkan oleh admin.'))
    audit(user['email'], f'Memulihkan akses AI customer #{user_id}')
    return {'ok': True}
