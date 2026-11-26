# Requirement Traceability Matrix

Matriks awal ini menghubungkan kebutuhan pada dokumen analisis dengan kondisi project yang diberikan. Status “Belum ada” berarti tidak ditemukan implementasi eksplisit saat meninjau file project; bukan klaim bahwa tidak mungkin ada di luar file yang diberikan.

| Kebutuhan pada dokumen analisis | Kondisi project yang terlihat | Next step |
|---|---|---|
| FastAPI modular monolith | FastAPI sudah ada pada `backend/app/main.py`; struktur masih relatif terpusat | Tambahkan modul terpisah secara bertahap tanpa mengubah endpoint lama sekaligus |
| React SPA + TypeScript + Tailwind | Frontend saat ini `frontend/index.html` | Pertahankan frontend sekarang; React dapat dibuat sebagai frontend tambahan setelah alur API stabil |
| PostgreSQL 16 + pgvector | SQLite (`backend/vteki.db`) | Rancang migrasi dan uji pada salinan data; jangan mengganti database langsung |
| Redis + Celery | Belum terlihat pada requirements/source | Tambahkan setelah proses sinkron dan kontrak tugas ditentukan |
| MinIO untuk bukti | Belum terlihat | Tambahkan upload dan object storage dengan zona karantina |
| ClamAV | Belum terlihat | Tambahkan pemindaian sebelum bukti tersedia untuk agen/LLM |
| AL0–AL3 + OTP | Login bearer dan role sudah ada; flow AL/OTP spesifik belum terlihat | Tambahkan state assurance, OTP simulator, lockout 3 kali/15 menit |
| 14 intent dan threshold confidence | Klasifikasi berbasis aturan sederhana dijelaskan di README | Definisikan 14 label; simpan confidence; implementasikan threshold 0.75 / 0.45–0.74 / <0.45 |
| RAG bersumber resmi | Knowledge base dan retrieval pencocokan kata sudah ada | Tambahkan metadata sumber/versi; kemudian pgvector/embedding sebagai modul terpisah |
| State machine tiket/closure checklist | Case dan beberapa status sudah ada | Definisikan transisi status sah dan checklist penutupan |
| SLA kalender kerja Asia/Jakarta | Belum terlihat | Tambahkan aturan kalender kerja, pause/resume, target SLA dan pengujian tanggal |
| Handoff paket 17 field | Admin dapat melihat/menangani tiket; skema 17 field belum terlihat | Tetapkan daftar 17 field dengan tim, lalu implementasikan payload dan workspace |
| Copilot dengan persetujuan agen | Chat Gemini sudah ada; draft Copilot agen spesifik belum terlihat | Tambahkan saran draft yang memerlukan tindakan manual agen |
| Dashboard SLA dan QA sampling | Dashboard admin dasar sudah ada | Tambahkan metrik yang dihitung dari timestamp/tiket nyata dan QA sampling |
| Audit trail | Tabel audit dan beberapa pencatatan aksi sudah ada | Pastikan seluruh aksi sensitif dan transisi status dicatat tanpa rahasia |
| Docker Compose | Panduan menjalankan lokal tersedia; compose belum terlihat | Tambahkan containerization setelah baseline lulus tes |

## Catatan penting

Dokumen analisis menyebut 14 taksonomi intent dan paket handoff 17 field, tetapi tidak memberikan daftar lengkap label intent atau seluruh 17 field secara eksplisit dalam teks yang tersedia. Tim perlu menyepakati definisi tersebut sebelum implementasi agar tidak mengarang persyaratan.
