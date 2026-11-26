"""Knowledge base: parsing file, pemecahan per bagian (chunking), dan pencarian BM25.

Alur upload:
  file (.md/.txt/.pdf/.docx) -> teks markdown -> dipecah per judul (##/###)
  -> tiap bagian jadi satu baris tabel `knowledge` lengkap dengan metadata
     (kategori, jenis, URL sumber, validitas, kata kunci)
  -> dokumen induk disimpan di `knowledge_documents` (versi, hash, teks asli).

Hanya bagian berjenis `fakta` yang dicari untuk menjawab customer. Bagian
berjenis `instruksi` (mis. "Instruksi untuk AI Agent") ditambahkan ke prompt
sebagai panduan, dan `metadata` tidak pernah dipakai.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import re
import threading
import unicodedata
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path

from .config import CHUNK_MAX_CHARS, KB_MIN_SCORE, KB_RELATIVE_CUTOFF, KB_TOP_K
from .db import connect

log = logging.getLogger("vteki.knowledge")

ALLOWED_EXT = {".md": "md", ".markdown": "md", ".txt": "txt", ".pdf": "pdf", ".docx": "docx"}

# ---------------------------------------------------------------------------
# Tokenisasi & stemming bahasa Indonesia
# ---------------------------------------------------------------------------
try:  # Sastrawi = stemmer bahasa Indonesia (pure python)
    from Sastrawi.Stemmer.StemmerFactory import StemmerFactory

    _stemmer = StemmerFactory().create_stemmer()

    @lru_cache(maxsize=50000)
    def stem(word: str) -> str:
        return _stemmer.stem(word) or word
except Exception:  # pragma: no cover - fallback bila paket belum terpasang
    _SUFFIX = re.compile(r"(lah|kah|tah|pun|nya|ku|mu)$")

    @lru_cache(maxsize=50000)
    def stem(word: str) -> str:
        w = _SUFFIX.sub("", word) if len(word) > 5 else word
        w = re.sub(r"(kan|an|i)$", "", w) if len(w) > 5 else w
        w = re.sub(r"^(di|ke|se|ter|ber|be|meng|meny|mem|men|me|peng|peny|pem|pen|pe)", "", w) if len(w) > 5 else w
        return w or word

STOP_WORDS = set("""
ada adalah agar akan aku anda apa apakah atau bagaimana bagi bahwa baik banyak beberapa begitu belum
berapa bisa boleh bukan cara dalam dan dapat dari dengan deh di dia dong engkau gimana hal hanya harus
ingin ini itu jadi jika juga kalau kami kamu kan kapan karena ke kepada kok lagi lah maka mana masih
mau melalui mereka mohon nya oleh pada para per perlu saat saja sama sangat saya sebagai secara sedang
sehingga sekarang selain semua seperti sih silakan sudah supaya tanya tapi tentang terhadap tersebut
tidak tolong untuk ya yaitu yang min kak gan bang mas mbak pak bu hai hi terima kasih makasih
the a an of to is are and or for in on what how
""".split())

# Ekspansi sinonim/singkatan umum layanan bank. Admin juga bisa menambah
# kata kunci per bagian lewat menu Basis Pengetahuan.
SYNONYMS = {
    "cs": "customer service halo kontak hubungi",
    "callcenter": "halo kontak telepon",
    "call": "halo telepon hubungi",
    "telp": "telepon kontak hubungi",
    "telpon": "telepon kontak hubungi",
    "nomor": "kontak hubungi",
    "wa": "whatsapp",
    "whatsapp": "kontak hubungi",
    "mbanking": "mobile m-bca aplikasi",
    "m-banking": "mobile m-bca aplikasi",
    "ibanking": "klikbca internet banking",
    "internet": "klikbca",
    "tabungan": "simpanan tahapan rekening",
    "nabung": "simpanan tahapan tabungan",
    "atm": "kartu paspor debit",
    "debit": "kartu paspor",
    "hilang": "blokir kartu hubungi",
    "dicuri": "blokir kartu hubungi",
    "kecurian": "blokir kartu hubungi",
    "penipu": "penipuan modus palsu",
    "scam": "penipuan modus palsu",
    "phising": "penipuan tautan palsu",
    "phishing": "penipuan tautan palsu",
    "link": "tautan",
    "hack": "penipuan keamanan akses",
    "dibobol": "penipuan transaksi tidak dikenal",
    "komplain": "pengaduan keluhan",
    "keluhan": "pengaduan",
    "lapor": "pengaduan laporan",
    "aman": "keamanan rahasia",
    "kode": "otp",
    "sandi": "password",
    "beda": "perbedaan",
    "admin": "biaya administrasi",
    "fee": "biaya",
    "resmi": "kanal resmi",
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.lower()


def tokenize(text: str, expand: bool = False) -> list[str]:
    raw = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", normalize_text(text))
    out: list[str] = []
    for w in raw:
        parts = [w, w.replace("-", "")] if "-" in w else [w]
        if expand and w in SYNONYMS:
            parts += SYNONYMS[w].split()
        for p in parts:
            if len(p) < 2 or p in STOP_WORDS:
                continue
            s = stem(p) if p.isalpha() else p
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Ekstraksi teks dari file
# ---------------------------------------------------------------------------
class KnowledgeFileError(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    ftype = ALLOWED_EXT.get(ext)
    if not ftype:
        raise KnowledgeFileError(f"Format {ext or '(tanpa ekstensi)'} belum didukung. Gunakan .md, .txt, .pdf, atau .docx.")
    if ftype in ("md", "txt"):
        for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
            try:
                return data.decode(enc), ftype
            except UnicodeDecodeError:
                continue
        raise KnowledgeFileError("Encoding file teks tidak dikenali (simpan sebagai UTF-8).")
    if ftype == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise KnowledgeFileError("Paket pypdf belum terpasang. Jalankan: pip install -r requirements.txt") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(p.extract_text() or "").strip() for p in reader.pages]
        except Exception as exc:
            raise KnowledgeFileError(f"PDF tidak dapat dibaca: {exc}") from exc
        text = "\n\n".join(p for p in pages if p)
        if len(text.strip()) < 20:
            raise KnowledgeFileError("PDF tidak berisi teks yang bisa dibaca (kemungkinan hasil scan). Konversi ke teks/markdown dulu.")
        return _pdf_to_markdown(text), ftype
    if ftype == "docx":
        try:
            import docx  # python-docx
        except ImportError as exc:
            raise KnowledgeFileError("Paket python-docx belum terpasang. Jalankan: pip install -r requirements.txt") from exc
        try:
            d = docx.Document(io.BytesIO(data))
        except Exception as exc:
            raise KnowledgeFileError(f"DOCX tidak dapat dibaca: {exc}") from exc
        lines = []
        for p in d.paragraphs:
            t = p.text.strip()
            if not t:
                lines.append("")
                continue
            style = (p.style.name or "").lower() if p.style is not None else ""
            m = re.match(r"heading\s*(\d)", style) or re.match(r"judul\s*(\d)", style)
            if style == "title":
                lines.append(f"# {t}")
            elif m:
                lines.append("#" * min(int(m.group(1)) + 1, 4) + " " + t)
            elif "list" in style:
                lines.append(f"- {t}")
            else:
                lines.append(t)
        for table in d.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
            lines.append("")
        return "\n".join(lines), ftype
    raise KnowledgeFileError("Format tidak didukung.")


def _pdf_to_markdown(text: str) -> str:
    """Tebak judul pada PDF: baris pendek bernomor ("2. Kanal bantuan") / FAQ ("Q1.")."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^\d{1,2}\.\s+[A-Z][^.]{2,70}$", s):
            out.append("## " + s)
        elif re.match(r"^Q\d{1,3}[.)]\s+.{5,160}\?$", s):
            out.append("### " + s)
        else:
            out.append(s)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Chunking per bagian
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"https?://[^\s)>\]`\"']+")
MONTHS = {"januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7,
          "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12}

CATEGORY_RULES = [
    ("Umum", r"identitas|informasi umum|profil|tentang (kami|dokumen)|batasan dokumen"),
    ("Sumber Resmi", r"sumber resmi|daftar sumber|referensi"),
    ("FAQ", r"\bfaq\b|pertanyaan (umum|yang sering)|^q\d"),
    ("Keamanan", r"keamanan|penipuan|modus|palsu|phishing|rahasia|otp|\bpin\b|kata sandi|password|mencurigakan"),
    ("Pengaduan", r"pengaduan|komplain|keluhan|tindak lanjut|sanggah"),
    ("Kontak", r"kanal bantuan|halo ?bca|kontak|hubungi|call ?cent|whatsapp|telepon|email resmi"),
    ("Layanan Digital", r"mybca|m-?bca|bca mobile|klikbca|e-?banking|mobile banking|internet banking|aplikasi|digital"),
    ("Simpanan", r"tabungan|simpanan|tahapan|deposito|giro|rekening"),
    ("Kartu", r"kartu kredit|kartu debit|\bkartu\b|paspor"),
    ("Pinjaman", r"kredit|pinjaman|kpr|kkb|cicilan"),
    ("Biaya & Limit", r"biaya|tarif|limit|bunga|suku bunga"),
]
INSTRUCTION_RE = re.compile(r"instruksi untuk (ai|agent|asisten)|instruksi (ai|agent)|panduan (untuk )?(ai|agent|asisten)|aturan (ai|agent|asisten)|jika jawaban tidak ditemukan|system prompt", re.I)
METADATA_RE = re.compile(r"metadata|skema database|struktur database", re.I)
DYNAMIC_RE = re.compile(r"biaya|limit|bunga|tarif|saldo minimum|setoran|nomor|kurs|promo|\b\d{4,}\b|dapat berubah", re.I)


def _clean_heading(h: str) -> str:
    h = re.sub(r"[*_`]", "", h).strip()
    h = re.sub(r"^\d{1,2}(\.\d+)*[.)]?\s+", "", h)  # "2. Kanal" -> "Kanal"
    return h.strip(" :—-") or h


def _clean_body(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def guess_category(title: str, body: str) -> str:
    t = normalize_text(title)
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, t):
            return name
    b = normalize_text(body)
    best, best_n = "Umum", 0
    for name, pattern in CATEGORY_RULES[3:]:
        n = len(re.findall(pattern, b))
        if n > best_n:
            best, best_n = name, n
    return best if best_n >= 2 else "Umum"


def guess_kind(title: str) -> str:
    if INSTRUCTION_RE.search(title):
        return "instruksi"
    if METADATA_RE.search(title):
        return "metadata"
    return "fakta"


def extract_keywords(title: str, body: str, n: int = 8) -> str:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9-]+", normalize_text(f"{title} {title} {title} {body}"))
             if len(w) > 3 and w not in STOP_WORDS and not w.isdigit() and not w.startswith("http")]
    return " ".join(w for w, _ in Counter(words).most_common(n))


def _parse_checked_at(text: str) -> str:
    m = re.search(r"(tanggal (penyusunan|pembaruan|diperbarui|update)|diperbarui|checked_at|last updated)\W{0,6}(\d{1,2})\s+([a-z]+)\s+(\d{4})", normalize_text(text[:2000]))
    if m and m.group(4) in MONTHS:
        try:
            return date(int(m.group(5)), MONTHS[m.group(4)], int(m.group(3))).isoformat()
        except ValueError:
            pass
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text[:2000])
    return m.group(0) if m else ""


def _split_long(body: str, limit: int) -> list[str]:
    if len(body) <= limit:
        return [body]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", body):
        if cur and len(cur) + len(para) + 2 > limit:
            parts.append(cur.strip())
            cur = ""
        while len(para) > limit:  # paragraf sangat panjang -> potong per kalimat
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 3 else limit
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        cur += ("\n\n" if cur else "") + para
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk_document(text: str, default_title: str = "Dokumen") -> dict:
    """Pecah markdown per judul. Kembalikan {'title','source_url','checked_at','chunks':[...]}."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    doc_title = default_title
    sections: list[dict] = []  # {h2, h3, lines}
    cur = {"h2": "", "h3": "", "lines": []}
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            cur["lines"].append(line)
            continue
        m = None if in_code else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1:
                if doc_title == default_title and not sections and not any(l.strip() for l in cur["lines"]):
                    doc_title = _clean_heading(heading)
                    continue
                level = 2
            sections.append(cur)
            if level == 2:
                cur = {"h2": heading, "h3": "", "lines": []}
            else:
                cur = {"h2": cur["h2"], "h3": heading, "lines": []}
            continue
        cur["lines"].append(line)
    sections.append(cur)

    preamble = "\n".join(sections[0]["lines"]) if sections and not sections[0]["h2"] else ""
    urls = URL_RE.findall(preamble)
    doc_source = urls[0].rstrip(".,;") if urls else ""
    checked_at = _parse_checked_at(text)

    # Dokumen tanpa judul sama sekali (txt/pdf polos) -> potong per ukuran.
    has_headings = any(s["h2"] or s["h3"] for s in sections)

    chunks = []
    for s in sections:
        body = _clean_body("\n".join(s["lines"]))
        if len(re.sub(r"\W", "", body)) < 15:
            continue
        h2, h3 = _clean_heading(s["h2"]), _clean_heading(s["h3"])
        if not h2 and not h3:
            title = "Tentang dokumen" if has_headings else doc_title
        elif h3:
            title = f"{h2} › {h3}" if h2 else h3
        else:
            title = h2
        section_path = " › ".join(x for x in (h2, h3) if x)
        kind = guess_kind(f"{h2} {h3}")
        pieces = _split_long(body, CHUNK_MAX_CHARS)
        for i, piece in enumerate(pieces):
            t = title if len(pieces) == 1 else f"{title} (bagian {i + 1})"
            src = URL_RE.findall(piece)
            chunks.append({
                "title": t[:200],
                "section": section_path[:300],
                "body": piece,
                "kind": kind,
                "category": "Instruksi AI" if kind == "instruksi" else "Metadata" if kind == "metadata" else ("Umum" if title == "Tentang dokumen" else guess_category(f"{h2} {h3}", piece)),
                "keywords": extract_keywords(h3 or t, piece),
                "source_url": (src[0].rstrip(".,;") if src else doc_source)[:500],
                "validity": "dinamis" if DYNAMIC_RE.search(piece) else "umum",
                "checked_at": checked_at,
                "status": "Draf" if kind == "metadata" else "Aktif",
            })
    return {"title": doc_title[:200], "source_url": doc_source, "checked_at": checked_at, "chunks": chunks}


# ---------------------------------------------------------------------------
# Simpan dokumen
# ---------------------------------------------------------------------------
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_document(*, filename: str, data: bytes | None = None, text: str | None = None,
                    title: str | None = None, status: str = "Aktif", mode: str = "auto",
                    category: str = "", uploaded_by: str = "system") -> dict:
    """mode: auto (ganti versi bila nama file sama) | new (selalu dokumen baru) | replace:<id>."""
    if text is None:
        if data is None:
            raise KnowledgeFileError("File kosong.")
        text, ftype = extract_text(filename, data)
    else:
        ftype = ALLOWED_EXT.get(Path(filename).suffix.lower(), "txt")
    if len(text.strip()) < 20:
        raise KnowledgeFileError("Isi dokumen terlalu pendek atau kosong.")
    digest = sha256(text.encode("utf-8"))
    parsed = chunk_document(text, default_title=title or Path(filename).stem.replace("_", " "))
    if not parsed["chunks"]:
        raise KnowledgeFileError("Tidak ada bagian yang dapat diambil dari dokumen.")
    doc_title = (title or parsed["title"])[:200]
    # Status dokumen: Aktif | Nonaktif. ("Draf" saat upload = dokumen disimpan tapi belum dipakai AI.)
    status = "Nonaktif" if status in ("Draf", "Nonaktif") else "Aktif"

    with connect() as c:
        existing = None
        if mode.startswith("replace:"):
            existing = c.execute("SELECT * FROM knowledge_documents WHERE id=?", (int(mode.split(":", 1)[1]),)).fetchone()
            if not existing:
                raise KnowledgeFileError("Dokumen yang akan diganti tidak ditemukan.")
        elif mode == "auto":
            existing = c.execute("SELECT * FROM knowledge_documents WHERE filename=? ORDER BY id DESC LIMIT 1", (filename,)).fetchone()
        if existing and existing["sha256"] == digest:
            return {"filename": filename, "document_id": existing["id"], "title": existing["title"],
                    "version": existing["version"], "chunks": existing["chunk_count"], "action": "unchanged"}
        if existing:
            doc_id, version = existing["id"], existing["version"] + 1
            c.execute("DELETE FROM knowledge WHERE document_id=?", (doc_id,))
            c.execute("""UPDATE knowledge_documents SET title=?, filename=?, file_type=?, sha256=?, version=?,
                         chunk_count=?, source_url=?, checked_at=?, raw_text=?, uploaded_by=?, status=?,
                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                      (doc_title, filename, ftype, digest, version, len(parsed["chunks"]), parsed["source_url"],
                       parsed["checked_at"], text, uploaded_by, status, doc_id))
            action = "replaced"
        else:
            cur = c.execute("""INSERT INTO knowledge_documents(title,filename,file_type,sha256,version,status,
                               chunk_count,source_url,checked_at,raw_text,uploaded_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (doc_title, filename, ftype, digest, 1, status, len(parsed["chunks"]), parsed["source_url"],
                             parsed["checked_at"], text, uploaded_by))
            doc_id, version, action = cur.lastrowid, 1, "created"
        for pos, ch in enumerate(parsed["chunks"]):
            c.execute("""INSERT INTO knowledge(title,category,keywords,body,status,document_id,section,kind,
                         source_url,validity,checked_at,position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (ch["title"], category if (category and ch["kind"] == "fakta") else ch["category"], ch["keywords"],
                       ch["body"], ch["status"], doc_id, ch["section"], ch["kind"], ch["source_url"],
                       ch["validity"], ch["checked_at"], pos))
    if not uploaded_by.startswith("sync:"):
        with connect() as c:
            c.execute("DELETE FROM app_meta WHERE key=?", (_tombstone_key(filename),))
    invalidate_index()
    return {"filename": filename, "document_id": doc_id, "title": doc_title, "version": version,
            "chunks": len(parsed["chunks"]), "action": action}


def _tombstone_key(filename: str) -> str:
    return "kb_deleted:" + filename


def mark_deleted(filename: str) -> None:
    """Catat bahwa dokumen ini dihapus admin agar sinkron folder tidak mengimpornya lagi."""
    with connect() as c:
        c.execute("INSERT INTO app_meta(key,value) VALUES(?,'1') ON CONFLICT(key) DO UPDATE SET value='1'", (_tombstone_key(filename),))


def active_fact_ids(ids, since: str = "") -> set:
    """Subset id bagian yang SAAT INI masih aktif dipakai AI (belum dihapus/dinonaktifkan).
    Bila `since` diisi, bagian yang diedit setelah waktu itu dianggap berubah (tidak lagi valid)."""
    ids = [int(i) for i in ids if str(i).lstrip("-").isdigit()]
    if not ids:
        return set()
    with connect() as c:
        rows = c.execute(f"""SELECT k.id FROM knowledge k LEFT JOIN knowledge_documents d ON d.id=k.document_id
                             WHERE k.id IN ({','.join('?' * len(ids))}) AND k.status='Aktif' AND k.kind='fakta'
                             AND (d.id IS NULL OR d.status='Aktif')
                             AND (?='' OR k.updated_at<=?)""", [*ids, since, since]).fetchall()
    return {r["id"] for r in rows}


def sync_knowledge_dir(folder: Path) -> list[dict]:
    """Impor otomatis file di folder knowledge/ (baru atau berubah) saat startup."""
    results = []
    if not folder.exists():
        return results
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXT or path.name.startswith(("~", ".")):
            continue
        with connect() as c:
            deleted = c.execute("SELECT 1 FROM app_meta WHERE key=?", (_tombstone_key(path.name),)).fetchone()
            current = c.execute("SELECT uploaded_by FROM knowledge_documents WHERE filename=? ORDER BY id DESC LIMIT 1", (path.name,)).fetchone()
        if deleted or (current and not (current["uploaded_by"] or "").startswith("sync:")):
            # Sudah dihapus admin, atau versinya sudah diganti lewat menu -> jangan ditimpa/dihidupkan lagi.
            results.append({"filename": path.name, "action": "skipped"})
            continue
        try:
            results.append(ingest_document(filename=path.name, data=path.read_bytes(), mode="auto", uploaded_by="sync:knowledge/"))
        except Exception as exc:
            log.warning("Gagal impor %s: %s", path.name, exc)
            results.append({"filename": path.name, "action": "error", "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# Pencarian BM25
# ---------------------------------------------------------------------------
_index_lock = threading.Lock()
_index = {"key": None, "docs": [], "df": Counter(), "avgdl": 1.0}


def invalidate_index():
    with _index_lock:
        _index["key"] = None


def _index_key(c):
    r = c.execute("SELECT COUNT(*) n, MAX(updated_at) u, SUM(LENGTH(body)+LENGTH(title)+LENGTH(IFNULL(keywords,''))+LENGTH(status)) s FROM knowledge").fetchone()
    d = c.execute("SELECT COUNT(*) n, MAX(updated_at) u, GROUP_CONCAT(status) s FROM knowledge_documents").fetchone()
    return (r["n"], r["u"], r["s"], d["n"], d["u"], d["s"])


def _build_index():
    with connect() as c:
        key = _index_key(c)
        with _index_lock:
            if _index["key"] == key:
                return _index
        rows = c.execute("""SELECT k.id,k.title,k.category,k.keywords,k.body,k.section,k.source_url,k.validity,
                                   k.checked_at,k.document_id,d.title AS document_title
                            FROM knowledge k LEFT JOIN knowledge_documents d ON d.id=k.document_id
                            WHERE k.status='Aktif' AND k.kind='fakta' AND (d.id IS NULL OR d.status='Aktif')""").fetchall()
    docs, df = [], Counter()
    for r in rows:
        r = dict(r)
        title_terms = tokenize(f"{r['title']} {r['section']}")
        kw_terms = tokenize(r["keywords"] or "")
        body_terms = tokenize(r["body"])
        # Bobot field: judul x3, kata kunci x2, isi x1
        tf = Counter()
        for t in title_terms:
            tf[t] += 3
        for t in kw_terms:
            tf[t] += 2
        for t in body_terms:
            tf[t] += 1
        r["_tf"], r["_len"], r["_title_terms"] = tf, sum(tf.values()), set(title_terms)
        docs.append(r)
        df.update(tf.keys())
    avgdl = (sum(d["_len"] for d in docs) / len(docs)) if docs else 1.0
    with _index_lock:
        _index.update(key=key, docs=docs, df=df, avgdl=avgdl)
    return _index


def search(query: str, extra_query: str = "", top_k: int = KB_TOP_K, min_score: float = KB_MIN_SCORE,
           relative_cutoff: float = KB_RELATIVE_CUTOFF, debug: bool = False) -> list[dict]:
    idx = _build_index()
    docs, df, avgdl = idx["docs"], idx["df"], idx["avgdl"]
    if not docs:
        return []
    q_terms = tokenize(query, expand=True)
    if extra_query:
        q_terms += [t for t in tokenize(extra_query, expand=True) if t not in q_terms]
    q_weights = Counter(q_terms)
    if not q_weights:
        return []
    N, k1, b = len(docs), 1.4, 0.6
    scored = []
    for d in docs:
        score, matched = 0.0, []
        for term, qw in q_weights.items():
            f = d["_tf"].get(term)
            if not f:
                continue
            idf = math.log(1 + (N - df[term] + 0.5) / (df[term] + 0.5))
            score += min(qw, 2) * idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d["_len"] / avgdl))
            matched.append(term)
        if not matched:
            continue
        # Bonus bila kata penting cocok dengan judul bagian
        title_hits = len(set(matched) & d["_title_terms"])
        score *= 1 + 0.15 * title_hits
        scored.append((score, matched, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < min_score:
        return [] if not debug else [_hit(s, m, d, False) for s, m, d in scored[:top_k]]
    top = scored[0][0]
    out = []
    used = 0
    for s, m, d in scored[: max(top_k, 8) if debug else top_k]:
        ok = s >= min_score and s >= top * relative_cutoff and used < top_k
        used += ok
        if ok or debug:
            out.append(_hit(s, m, d, ok))
    return out[:top_k] if not debug else out


def _hit(score, matched, d, used):
    return {"id": d["id"], "title": d["title"], "category": d["category"], "section": d["section"],
            "body": d["body"], "source_url": d["source_url"], "validity": d["validity"],
            "checked_at": d["checked_at"], "document_id": d["document_id"], "document_title": d["document_title"],
            "score": round(score, 2), "matched": sorted(set(matched)), "used": used}


def instruction_chunks(limit_chars: int = 3500) -> str:
    """Panduan dari bagian berjenis 'instruksi' (dokumen aktif) untuk ditambahkan ke prompt."""
    with connect() as c:
        rows = c.execute("""SELECT k.title,k.body FROM knowledge k LEFT JOIN knowledge_documents d ON d.id=k.document_id
                            WHERE k.status='Aktif' AND k.kind='instruksi' AND (d.id IS NULL OR d.status='Aktif')
                            ORDER BY k.document_id, k.position""").fetchall()
    text, total = [], 0
    for r in rows:
        block = f"[{r['title']}]\n{r['body']}"
        if total + len(block) > limit_chars:
            break
        text.append(block)
        total += len(block)
    return "\n\n".join(text)


def kb_stats() -> dict:
    with connect() as c:
        return {
            "documents": c.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0],
            "active_chunks": c.execute("""SELECT COUNT(*) FROM knowledge k LEFT JOIN knowledge_documents d ON d.id=k.document_id
                                          WHERE k.status='Aktif' AND k.kind='fakta' AND (d.id IS NULL OR d.status='Aktif')""").fetchone()[0],
        }


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
