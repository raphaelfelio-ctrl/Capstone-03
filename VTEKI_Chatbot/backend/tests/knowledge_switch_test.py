"""Uji pergantian knowledge: apakah AI menjawab customer dengan knowledge yang BARU/AKTIF.

Jalankan dari folder backend:
    python -m tests.knowledge_switch_test

Gemini ditiru (tidak memakai kuota). LLM tiruan merekam setiap prompt sehingga uji
bisa memastikan knowledge lama tidak lagi sampai ke AI (di <kb>, riwayat, maupun panduan).
Database uji dibuat sementara; vteki.db tidak tersentuh.
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(Path(tmp) / "switch.db")
os.environ["GEMINI_API_KEY"] = "dummy-key-untuk-uji"
os.environ["LLM_CLASSIFIER"] = "true"
os.environ["RATE_CHAT_PER_MIN"] = "1000"
os.environ["STORAGE_DIR"] = str(Path(tmp) / "storage")

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, knowledge, llm  # noqa: E402

CALLS = []  # (jenis, system, prompt)


def fake_generate_json(system, prompt, **kw):
    msg = re.search(r"<pesan_customer>\n(.*?)\n</pesan_customer>", prompt, re.S).group(1)
    if system.startswith("Anda adalah pengklasifikasi"):
        CALLS.append(("classify", system, prompt))
        return {"intent": agent.rule_intent(msg), "search_query": msg, "manipulation": False,
                "manipulation_confidence": 0.0, "manipulation_reason": ""}
    CALLS.append(("generate", system, prompt))
    arts = re.findall(r'<artikel id="(\d+)" judul="([^"]*)"[^>]*>\n(.*?)\n</artikel>', prompt, re.S)
    if "pakai-nomor-lama" in msg:  # LLM "ingat" nomor lama dari pengetahuannya sendiri
        return {"answer": "Silakan hubungi call center 1500-777.", "grounded": True,
                "source_ids": [int(arts[0][0])] if arts else [], "needs_human": False, "manipulation": False}
    if not arts:
        return {"answer": "Informasi belum tersedia.", "grounded": False, "source_ids": [], "needs_human": True, "manipulation": False}
    aid, title, body = arts[0]
    return {"answer": f"Berdasarkan '{title}': {body[:220]}", "grounded": True, "source_ids": [int(aid)],
            "needs_human": False, "manipulation": False}


llm.generate_json = fake_generate_json
llm.is_configured = lambda: True

from app.main import app  # noqa: E402

ok = fail = 0
FINDINGS = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]    {name}")
    else:
        fail += 1
        print(f"  [GAGAL] {name}  {detail}")


def note(name, cond, detail=""):
    """Bukan kegagalan fungsi utama, tapi temuan yang perlu diketahui."""
    print(f"  [{'OK' if cond else 'TEMUAN'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FINDINGS.append(name)


def last_generate():
    for kind, system, prompt in reversed(CALLS):
        if kind == "generate":
            return system, prompt
    return "", ""


def kb_part(prompt):
    m = re.search(r"<kb>.*?</kb>", prompt, re.S)
    return m.group(0) if m else ""


def history_part(prompt):
    m = re.search(r"<riwayat>.*?</riwayat>", prompt, re.S)
    return m.group(0) if m else ""


def kb_doc(no, wa, app_name="SejahteraMobile", marker="PANDUAN-SEJAHTERA-V1"):
    return f"""# Knowledge Base Bank Sejahtera
**Tanggal penyusunan:** 10 November 2026
**Sumber utama:** https://www.banksejahtera.example/

## 0. Instruksi untuk AI Agent
{marker}: selalu sebut nama Bank Sejahtera dan arahkan ke kanal resmi Bank Sejahtera.

## 2. Kanal bantuan resmi
### Call Center Sejahtera
Nomor call center resmi Bank Sejahtera adalah {no}, tersedia 24 jam. Sumber: https://www.banksejahtera.example/kontak

### WhatsApp resmi
WhatsApp resmi Bank Sejahtera: {wa}.

## 3. Layanan digital
### {app_name}
{app_name} adalah aplikasi mobile banking Bank Sejahtera untuk transfer, cek saldo, dan bayar tagihan.
""".encode()


NEW_FILE = "Sejahtera_KB.md"

with TestClient(app) as client:
    def login(email, pw):
        r = client.post("/api/auth/login", json={"email": email, "password": pw})
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    A = login("admin@vteki.id", "admin123")
    C = login("customer@vteki.id", "cust1234")
    chat = lambda msg, case=None: client.post("/api/chat", headers=C, json={"message": msg, **({"case_id": case} if case else {})}).json()
    docs = lambda: client.get("/api/knowledge/documents", headers=A).json()["items"]
    set_status = lambda did, st: client.patch(f"/api/knowledge/documents/{did}", headers=A, json={"status": st})

    bca = next(d for d in docs() if "BCA" in d["title"])

    print("1. Baseline: knowledge BCA aktif")
    r = chat("Berapa nomor Halo BCA?")
    case_lama = r["case_id"]
    check("dijawab dari dokumen BCA", r["sources"] and all("BCA" in s["document_title"] for s in r["sources"]), str(r["sources"]))
    check("jawaban memuat nomor Halo BCA 1500888", "1500888" in r["reply"].replace("-", "").replace(" ", ""), r["reply"][:200])

    print("2. Ganti knowledge: unggah Bank Sejahtera, nonaktifkan BCA")
    up = client.post("/api/knowledge/upload", headers=A, files=[("files", (NEW_FILE, kb_doc("1500-777", "0811-2233-4455"), "text/markdown"))]).json()
    new_id = up["results"][0]["document_id"]
    check("dokumen baru terunggah", up["results"][0]["action"] == "created", str(up))
    set_status(bca["id"], "Nonaktif")
    st = client.post("/api/knowledge/search-test", headers=A, json={"query": "berapa nomor halo bca"}).json()
    check("uji pencarian: tidak ada bagian BCA yang dipakai", not any("BCA" in (h["document_title"] or "") and h["used"] for h in st["hits"]), str([h["title"] for h in st["hits"]]))

    print("3. Percakapan BARU setelah pergantian")
    r = chat("Berapa nomor call center?")
    system, prompt = last_generate()
    check("sumber jawaban hanya dokumen baru", r["sources"] and all(s["document_title"] == "Knowledge Base Bank Sejahtera" for s in r["sources"]), str(r["sources"]))
    check("jawaban memuat nomor baru 1500-777", "1500-777" in r["reply"], r["reply"][:200])
    check("<kb> tidak memuat isi BCA", "BCA" not in kb_part(prompt) and "1500888" not in kb_part(prompt))
    check("panduan instruksi dokumen baru dipakai", "PANDUAN-SEJAHTERA-V1" in system)
    check("panduan instruksi dokumen BCA tidak dipakai", "Halo BCA" not in system.split("PANDUAN DARI DOKUMEN")[-1])
    r = chat("Apa itu SejahteraMobile?")
    check("pertanyaan produk baru dijawab (knowledge kecil, ambang menyesuaikan)", r["status"] == "Selesai otomatis" and r["sources"], str(r))
    r = chat("berapa harga tiket pesawat ke bali")
    check("knowledge kecil: pertanyaan tidak relevan tetap tidak dijawab", not r["sources"] and r["status"] == "Menunggu agen", str(r))

    print("4. Pertanyaan yang hanya ada di knowledge lama")
    r = chat("Apa itu KlikBCA?")
    check("tidak dijawab dari knowledge lama (diteruskan ke agen)", not r["sources"] and r["status"] == "Menunggu agen", str(r))
    check("jawaban tidak menyebut KlikBCA sebagai fakta", "klikbca adalah" not in r["reply"].lower(), r["reply"][:200])

    print("5. Melanjutkan percakapan LAMA (riwayat berisi jawaban BCA)")
    r = chat("Kalau nomor call center yang berlaku sekarang berapa?", case_lama)
    system, prompt = last_generate()
    check("jawaban pakai knowledge baru", "1500-777" in r["reply"], r["reply"][:200])
    check("jawaban AI lama (BCA) dibuang dari riwayat", "1500888" not in history_part(prompt).replace("-", "").replace(" ", ""), history_part(prompt)[:400])

    print("6. Versi baru dokumen yang sama (nomor berubah)")
    case_v = chat("Berapa nomor call center?")["case_id"]
    up = client.post("/api/knowledge/upload", headers=A, files=[("files", (NEW_FILE, kb_doc("1500-999", "0811-2233-4455", marker="PANDUAN-SEJAHTERA-V2"), "text/markdown"))]).json()
    check("unggah nama sama = versi 2", up["results"][0]["action"] == "replaced" and up["results"][0]["version"] == 2, str(up))
    r = chat("Berapa nomor call center sekarang?", case_v)
    system, prompt = last_generate()
    check("jawaban memakai nomor versi baru 1500-999", "1500-999" in r["reply"] and "1500-777" not in r["reply"], r["reply"][:200])
    check("nomor versi lama tidak ada di <kb>", "1500-777" not in kb_part(prompt))
    check("jawaban versi lama dibuang dari riwayat", "1500-777" not in history_part(prompt), history_part(prompt)[:400])
    check("panduan versi baru menggantikan versi lama", "PANDUAN-SEJAHTERA-V2" in system and "PANDUAN-SEJAHTERA-V1" not in system)
    r = chat("pakai-nomor-lama: berapa nomor call center?")
    check("output guard memblok nomor lama yang 'diingat' LLM", "1500-777" not in r["reply"] and r["status"] == "Menunggu agen", r["reply"][:200])

    print("7. Edit satu bagian lewat menu (tanpa unggah ulang)")
    items = client.get("/api/knowledge", headers=A, params={"document_id": new_id}).json()["items"]
    wa = next(i for i in items if "WhatsApp" in i["title"])
    case_e = chat("Berapa WhatsApp resmi?")["case_id"]
    time.sleep(1.1)  # updated_at beresolusi detik
    body = {k: wa[k] for k in ("title", "category", "keywords", "status", "kind", "source_url", "validity")}
    body["body"] = "WhatsApp resmi Bank Sejahtera: 0811-9999-0000."
    check("edit bagian berhasil", client.put(f"/api/knowledge/{wa['id']}", headers=A, json=body).status_code == 200)
    r = chat("Berapa WhatsApp resmi?", case_e)
    _, prompt = last_generate()
    check("jawaban memakai isi hasil edit", "0811-9999-0000" in r["reply"], r["reply"][:200])
    check("jawaban sebelum edit dibuang dari riwayat", "0811-2233-4455" not in history_part(prompt), history_part(prompt)[:400])

    # Edit yang terjadi di detik yang sama dengan jawaban sebelumnya
    case_s = chat("Berapa WhatsApp resmi?")["case_id"]
    body["body"] = "WhatsApp resmi Bank Sejahtera: 0811-5555-1111."
    client.put(f"/api/knowledge/{wa['id']}", headers=A, json=body)
    r = chat("Berapa WhatsApp resmi?", case_s)
    _, prompt = last_generate()
    check("edit cepat (detik yang sama): jawaban tetap memakai isi baru", "0811-5555-1111" in r["reply"], r["reply"][:200])
    check("edit cepat (detik yang sama): jawaban lama dibuang dari riwayat",
          "0811-9999-0000" not in history_part(prompt), history_part(prompt)[:400])

    print("8. Kembali ke knowledge BCA")
    set_status(new_id, "Nonaktif")
    set_status(bca["id"], "Aktif")
    r = chat("Berapa nomor Halo BCA?")
    _, prompt = last_generate()
    check("dijawab lagi dari BCA", r["sources"] and all("BCA" in s["document_title"] for s in r["sources"]), str(r["sources"]))
    check("isi Bank Sejahtera tidak ada di <kb>", "Sejahtera" not in kb_part(prompt))
    r = chat("Apa itu SejahteraMobile?")
    check("produk Sejahtera tidak dijawab lagi", not r["sources"] and r["status"] == "Menunggu agen", str(r))

    print("9. Identitas organisasi di prompt & template (ORG_NAME)")
    set_status(bca["id"], "Nonaktif")
    set_status(new_id, "Aktif")
    chat("Berapa nomor call center?")
    system, _ = last_generate()
    rules = system.split("PANDUAN DARI DOKUMEN")[0]
    check("aturan inti memakai organisasi knowledge aktif", "Bank Sejahtera" in rules and "BCA" not in rules,
          str(sorted(set(re.findall(r'[A-Za-z]*BCA[A-Za-z]*', rules)))))
    r = chat("halo")
    check("sapaan menyebut organisasi knowledge aktif", "Bank Sejahtera" in r["reply"] and "BCA" not in r["reply"], r["reply"][:120])
    check("dasbor admin menampilkan organisasi aktif", client.get("/api/admin/dashboard", headers=A).json()["org"] == "Bank Sejahtera")
    client.patch(f"/api/knowledge/documents/{new_id}", headers=A, json={"org_name": "Bank Sejahtera Makmur"})
    chat("Berapa nomor call center?")
    check("organisasi bisa diubah admin per dokumen", "Bank Sejahtera Makmur" in last_generate()[0])
    set_status(new_id, "Nonaktif")
    set_status(bca["id"], "Aktif")
    r = chat("halo")
    check("kembali ke BCA: sapaan menyebut BCA lagi", "BCA" in r["reply"] and "Sejahtera" not in r["reply"], r["reply"][:120])
    set_status(bca["id"], "Nonaktif")
    set_status(new_id, "Aktif")

    print("10. Dua dokumen aktif bersamaan (lupa menonaktifkan yang lama)")
    set_status(bca["id"], "Aktif")
    r = chat("Berapa nomor call center resmi?")
    _, prompt = last_generate()
    mixed = "BCA" in kb_part(prompt) and "Sejahtera" in kb_part(prompt)
    note("hanya satu organisasi yang masuk ke <kb>", not mixed,
         "-> <kb> berisi artikel BCA DAN Sejahtera; admin wajib menonaktifkan dokumen lama (perilaku sesuai desain)")
    set_status(bca["id"], "Nonaktif")

    print("11. Pergantian lewat folder knowledge/")
    folder = Path(tmp) / "kfolder"
    folder.mkdir()
    (folder / "Lama_KB.md").write_bytes(kb_doc("1500-111", "0811-1111-1111", app_name="LamaMobile"))
    knowledge.sync_knowledge_dir(folder)
    (folder / "Lama_KB.md").unlink()  # admin mengganti file di folder
    (folder / "Baru_KB.md").write_bytes(kb_doc("1500-222", "0811-2222-2222", app_name="BaruMobile"))
    knowledge.sync_knowledge_dir(folder)
    lama = next(d for d in docs() if d["filename"] == "Lama_KB.md")
    check("file yang dihapus dari folder knowledge/ ikut nonaktif", lama["status"] == "Nonaktif", str(lama))
    r = chat("Apa itu LamaMobile?")
    check("AI tidak lagi menjawab dari file folder yang sudah dihapus", not r["sources"], str(r["sources"]))
    r = chat("Apa itu BaruMobile?")
    check("AI menjawab dari file baru di folder", r["sources"] and r["status"] == "Selesai otomatis", str(r))
    check("dokumen di folder lain (BCA) tidak tersentuh", next(d for d in docs() if d["id"] == bca["id"])["status"] == "Nonaktif")
    (folder / "Lama_KB.md").write_bytes(kb_doc("1500-111", "0811-1111-1111", app_name="LamaMobile"))
    res = knowledge.sync_knowledge_dir(folder)
    check("file dikembalikan ke folder -> dokumen aktif lagi",
          next(d for d in docs() if d["filename"] == "Lama_KB.md")["status"] == "Aktif", str(res))
    baru = next(d for d in docs() if d["filename"] == "Baru_KB.md")
    set_status(baru["id"], "Nonaktif")  # admin menonaktifkan manual
    knowledge.sync_knowledge_dir(folder)
    check("dokumen yang dinonaktifkan admin tidak dihidupkan oleh sinkron",
          next(d for d in docs() if d["id"] == baru["id"])["status"] == "Nonaktif")

print(f"\nHasil: {ok} lulus, {fail} gagal, {len(FINDINGS)} temuan")
for f in FINDINGS:
    print("  - TEMUAN:", f)
sys.exit(1 if fail else 0)
