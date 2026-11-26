"""Skrip uji alur LangGraph (AI vs CS vs Admin).
Jalankan dari folder backend:
    python test_graph.py
"""

from app.main import vteki_graph

print("=" * 60)
print("PENGUJIAN ALUR PERCAKAPAN LANGGRAPH (VTEKI_CHATBOT)")
print("=" * 60)

# Skenario 1: Pertanyaan umum yang ada di Knowledge Base (Jalur AI)
print("\n[1] Menguji Jalur AI (Pertanyaan FAQ)...")
state_1 = {
    "message": "Berapa biaya tahunan kartu kredit?",
    "case_id": "TEST-001",
    "history": [],
    "hits": [],
    "reply": "",
    "status": "",
    "intent": "",
    "priority": "",
    "risk": 0,
    "route": "",
}
res_1 = vteki_graph.invoke(state_1)
print(f" -> Rute Dipilih : {res_1['route']}")
print(f" -> Status Akhir : {res_1['status']}")
print(f" -> Cuplikan AI  : {res_1['reply'][:90]}...")

# Skenario 2: Pertanyaan komplain (Jalur Eskalasi CS)
print("\n[2] Menguji Jalur Agen CS (Komplain)...")
state_2 = {
    "message": "Saya kecewa dan komplain tagihan saya terpotong dua kali!",
    "case_id": "TEST-002",
    "history": [],
    "hits": [],
    "reply": "",
    "status": "",
    "intent": "",
    "priority": "",
    "risk": 0,
    "route": "",
}
res_2 = vteki_graph.invoke(state_2)
print(f" -> Rute Dipilih : {res_2['route']}")
print(f" -> Status Akhir : {res_2['status']}")
print(f" -> Pesan Balasan: {res_2['reply'][:90]}...")

# Skenario 3: Permintaan risiko tinggi (Jalur Eskalasi Admin/Persetujuan)
print("\n[3] Menguji Jalur Admin (Risiko Tinggi: Blokir & Refund)...")
state_3 = {
    "message": "Tolong segera blokir kartu saya dan proses refund dana!",
    "case_id": "TEST-003",
    "history": [],
    "hits": [],
    "reply": "",
    "status": "",
    "intent": "",
    "priority": "",
    "risk": 0,
    "route": "",
}
res_3 = vteki_graph.invoke(state_3)
print(f" -> Rute Dipilih : {res_3['route']}")
print(f" -> Status Akhir : {res_3['status']}")
print(f" -> Pesan Balasan: {res_3['reply'][:90]}...")

# Skenario 4: Permintaan langsung berbicara dengan Admin (Eskalasi & Pembuatan Tiket)
print("\n[4] Menguji Permintaan Bicara dengan Admin...")
state_4 = {
    "message": "Halo, saya mau berbicara dengan admin sekarang",
    "case_id": "TEST-004",
    "history": [],
    "hits": [],
    "reply": "",
    "status": "",
    "intent": "",
    "priority": "",
    "risk": 0,
    "route": "",
}
res_4 = vteki_graph.invoke(state_4)
print(f" -> Rute Dipilih : {res_4['route']}")
print(f" -> Status Akhir : {res_4['status']}")
print(f" -> Pesan Balasan: {res_4['reply'][:90]}...")

print("\n" + "=" * 60)
print("SEMUA PENGUJIAN LANGGRAPH SELESAI DENGAN SUKSES!")
print("=" * 60)
