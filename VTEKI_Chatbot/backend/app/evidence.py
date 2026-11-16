"""Karantina & pemindaian bukti nasabah (spesifikasi §3.3, UC-04).

Alur:  unggah -> zona KARANTINA -> pindai (async) -> bersih: pindah ke zona AMAN + ekstrak teks untuk agen
                                                   -> terinfeksi/mencurigakan: berkas dihapus, status Ditolak
- Penyimpanan: adapter lokal (folder storage/quarantine & storage/clean). Struktur kunci objek sama dengan
  bucket S3/MinIO sehingga bisa diganti tanpa mengubah alur.
- Pemindai: daemon ClamAV (clamd, protokol INSTREAM) bila CLAMAV_HOST diisi; bila tidak, pemindai bawaan
  (signature EICAR, berkas eksekusi, makro Office, JavaScript di PDF, tipe isi tidak cocok dengan ekstensi).
- Berkas/teks bukti TIDAK PERNAH dimasukkan ke prompt LLM (mencegah prompt injection); hanya agen manusia
  yang dapat membaca teks hasil ekstraksi di Workspace.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import socket
import struct
import zipfile
from pathlib import Path

from .config import CLAMAV_HOST, CLAMAV_PORT, EVIDENCE_MAX_MB, STORAGE_DIR
from .db import connect
from .identity import now, ts

log = logging.getLogger("vteki.evidence")

ALLOWED = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".txt": "text/plain", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAGIC = {".pdf": [b"%PDF-"], ".png": [b"\x89PNG\r\n\x1a\n"], ".jpg": [b"\xff\xd8\xff"], ".jpeg": [b"\xff\xd8\xff"],
         ".docx": [b"PK\x03\x04"], ".txt": []}
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class EvidenceError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Penyimpanan (adapter lokal, kompatibel pola kunci S3/MinIO)
# ---------------------------------------------------------------------------
def _path(zone: str, key: str) -> Path:
    p = (STORAGE_DIR / zone / key).resolve()
    if STORAGE_DIR.resolve() not in p.parents:
        raise EvidenceError("Kunci penyimpanan tidak valid.")
    return p


def put(zone: str, key: str, data: bytes):
    p = _path(zone, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def get(zone: str, key: str) -> bytes:
    return _path(zone, key).read_bytes()


def move(src_zone: str, dst_zone: str, key: str):
    dst = _path(dst_zone, key)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(_path(src_zone, key)), str(dst))


def delete(zone: str, key: str):
    p = _path(zone, key)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Pemindai
# ---------------------------------------------------------------------------
def _clamd_scan(data: bytes) -> tuple[bool, str]:
    with socket.create_connection((CLAMAV_HOST, CLAMAV_PORT), timeout=30) as s:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 8192):
            chunk = data[i:i + 8192]
            s.sendall(struct.pack("!L", len(chunk)) + chunk)
        s.sendall(struct.pack("!L", 0))
        reply = s.recv(4096).decode(errors="replace").strip("\0\n ")
    # contoh balasan: "stream: OK" atau "stream: Eicar-Test-Signature FOUND"
    return reply.endswith("OK"), reply


def _builtin_scan(filename: str, data: bytes) -> tuple[bool, str]:
    ext = Path(filename).suffix.lower()
    if EICAR in data:
        return False, "Eicar-Test-Signature FOUND"
    head = data[:8]
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF") or head.startswith(b"#!"):
        return False, "Executable.Content FOUND"
    sigs = MAGIC.get(ext, [])
    if sigs and not any(data.startswith(s) for s in sigs):
        return False, "ContentType.Mismatch FOUND (isi berkas tidak sesuai ekstensi)"
    if ext == ".pdf" and re.search(rb"/(JavaScript|JS|Launch|EmbeddedFile)\b", data):
        return False, "PDF.ActiveContent FOUND"
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if any("vbaProject" in n for n in names):
                    return False, "Office.Macro FOUND"
                if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                    return False, "Archive.Bomb FOUND"
        except zipfile.BadZipFile:
            return False, "Docx.Corrupt FOUND"
    if ext == ".txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False, "Text.Binary FOUND"
    return True, "OK"


def scan(filename: str, data: bytes) -> tuple[bool, str, str]:
    """(bersih?, hasil, nama pemindai). Bila clamd dikonfigurasi tapi gagal dihubungi -> pemindai bawaan."""
    if CLAMAV_HOST:
        try:
            ok, res = _clamd_scan(data)
            if ok:  # tetap jalankan pemeriksaan tipe isi
                ok2, res2 = _builtin_scan(filename, data)
                return ok2, (res if ok2 else res2), "clamav+bawaan"
            return ok, res, "clamav"
        except OSError as exc:
            log.warning("ClamAV tidak dapat dihubungi (%s); memakai pemindai bawaan.", exc)
    ok, res = _builtin_scan(filename, data)
    return ok, res, "bawaan"


def _extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg"):
        return "(gambar — tidak ada teks yang diekstrak; buka berkas untuk melihat)"
    try:
        from .knowledge import extract_text
        text, _ = extract_text(filename if ext != ".txt" else filename, data)
        return text[:8000]
    except Exception as exc:
        return f"(teks tidak dapat diekstrak: {exc})"


# ---------------------------------------------------------------------------
# Alur
# ---------------------------------------------------------------------------
def accept_upload(*, case_id: str, user_id: int, filename: str, data: bytes) -> dict:
    name = Path(filename or "bukti").name[:120]
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise EvidenceError(f"Format {ext or '(tanpa ekstensi)'} tidak diizinkan. Gunakan PDF, PNG, JPG, TXT, atau DOCX.")
    if not data:
        raise EvidenceError("Berkas kosong.")
    if len(data) > EVIDENCE_MAX_MB * 1024 * 1024:
        raise EvidenceError(f"Ukuran berkas melebihi {EVIDENCE_MAX_MB} MB.", 413)
    digest = hashlib.sha256(data).hexdigest()
    with connect() as c:
        cur = c.execute("""INSERT INTO evidence(case_id,user_id,filename,mime,size,sha256,status,storage_key,created_at)
                           VALUES(?,?,?,?,?,?,'Karantina','',?)""",
                        (case_id, user_id, name, ALLOWED[ext], len(data), digest, ts(now())))
        eid = cur.lastrowid
        key = f"{case_id}/{eid}-{digest[:12]}{ext}"
        c.execute("UPDATE evidence SET storage_key=? WHERE id=?", (key, eid))
    put("quarantine", key, data)
    return {"id": eid, "filename": name, "status": "Karantina", "size": len(data)}


def process(evidence_id: int) -> dict:
    """Dipanggil asinkron (background task): pindai, lalu pindahkan ke zona aman atau tolak."""
    with connect() as c:
        ev = c.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not ev or ev["status"] != "Karantina":
            return dict(ev) if ev else {}
        c.execute("UPDATE evidence SET status='Memindai' WHERE id=?", (evidence_id,))
    try:
        data = get("quarantine", ev["storage_key"])
        ok, result, scanner = scan(ev["filename"], data)
    except Exception as exc:  # pemindaian gagal -> tetap di karantina, jangan lolos
        with connect() as c:
            c.execute("UPDATE evidence SET status='Karantina', scan_result=? WHERE id=?", (f"Gagal memindai: {exc}", evidence_id))
        return {"id": evidence_id, "status": "Karantina"}
    if ok:
        move("quarantine", "clean", ev["storage_key"])
        text = _extract_text(ev["filename"], data)
        status = "Bersih"
    else:
        delete("quarantine", ev["storage_key"])
        text, status = "", "Ditolak"
    with connect() as c:
        c.execute("UPDATE evidence SET status=?, scanner=?, scan_result=?, text_excerpt=?, scanned_at=? WHERE id=?",
                  (status, scanner, result[:300], text, ts(now()), evidence_id))
    log.info("Bukti #%s %s (%s: %s)", evidence_id, status, scanner, result)
    return {"id": evidence_id, "status": status, "scanner": scanner, "result": result}


def read_clean(evidence_id: int) -> tuple[dict, bytes]:
    with connect() as c:
        ev = c.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    if not ev:
        raise EvidenceError("Bukti tidak ditemukan.", 404)
    if ev["status"] != "Bersih":
        raise EvidenceError("Hanya berkas yang sudah dinyatakan bersih yang dapat dibuka.", 409)
    return dict(ev), get("clean", ev["storage_key"])
