# V-TEKI Chatbot — Next Steps Pengembangan

Dokumen ini dibuat berdasarkan dua sumber yang diberikan: project `VTEKI_Chatbot` dan dokumen *Analisis Kebutuhan Sistem (Tim 3 Orang) - AI Enterprise Customer Support* (26 Oktober 2026).

## Prinsip pengerjaan

- Implementasi yang sudah ada tetap dipertahankan.
- Tahapan di bawah adalah penambahan bertahap, bukan penggantian total.
- Jangan mengklaim sistem sudah terhubung ke core banking atau aman untuk produksi. Data, approval, dan tindakan perbankan yang ada masih berupa simulasi.
- Setiap fitur baru sebaiknya dibuat dalam modul terpisah dan diintegrasikan melalui endpoint yang sudah ada setelah diuji.

## Kondisi project saat ini

Berdasarkan source code dan README yang ada, project saat ini sudah memiliki:

- FastAPI backend, frontend HTML/JavaScript satu file, SQLite, dan integrasi Gemini.
- Login bearer token dan pembatasan role Admin/Customer.
- Tiket/case, riwayat pesan, notifikasi, pengelolaan artikel knowledge base, audit trail.
- Retrieval knowledge base berbasis pencocokan kata, belum embedding/vector search.
- Penanganan sederhana untuk kasus berisiko dan approval demo.

Belum terlihat implementasi nyata untuk beberapa kebutuhan di dokumen analisis: level jaminan identitas AL0–AL3 dengan OTP dan lockout, klasifikasi 14 intent beserta threshold confidence, loop klarifikasi terstruktur, RAG berbasis pgvector, SLA kalender kerja Asia/Jakarta, paket handoff 17 field, upload bukti dengan karantina MinIO dan pemindaian ClamAV, antrean Redis/Celery, workspace agen khusus, serta analitik QA/SLA lengkap.

## Urutan prioritas yang disarankan

### P0 — Amankan dan stabilkan baseline (kerjakan pertama)

1. **Rotasi Gemini API key** yang pernah tersimpan dalam file konfigurasi lokal/arsip. Buat key baru jika key lama pernah dibagikan atau diunggah.
2. Pastikan `backend/.env` hanya berada di komputer lokal; jangan masukkan ke Git atau arsip yang dibagikan. Gunakan `.env.example` sebagai template.
3. Ganti `APP_SECRET` dengan nilai acak yang kuat. Ganti password akun demo sebelum demo ke pihak lain.
4. Buat salinan cadangan `backend/vteki.db` sebelum perubahan skema.
5. Buat baseline pengujian manual dari `docs/TEST_SCENARIOS.md`; catat hasil aktual sebelum menambah fitur.
6. Pastikan seluruh endpoint privat menguji token dan role. Tambahkan pembatasan percobaan login, validasi input, dan batas ukuran request.

**Kriteria selesai:** aplikasi yang sekarang tetap bisa dijalankan, login Admin/Customer berfungsi, chat/knowledge/tiket/audit tetap berfungsi, dan rahasia tidak ikut dibagikan.

### P1 — Selaraskan tiket dan workflow CRM

1. Tetapkan daftar status tiket yang konsisten, misalnya `Baru`, `Menunggu klarifikasi`, `Menunggu agen`, `Menunggu persetujuan`, `Ditangani`, `Selesai`, dan `Ditolak`.
2. Tambahkan validasi transisi status agar tiket tidak bisa berpindah ke status yang tidak sah.
3. Simpan intent, confidence score, alasan eskalasi, waktu respons pertama, dan waktu penyelesaian pada case.
4. Tambahkan checklist penutupan tiket dan catat setiap perubahan penting pada audit trail.
5. Tambahkan pengujian kepemilikan tiket: Customer hanya boleh membaca tiket miliknya; Admin boleh mengakses tiket operasional sesuai role.

**Kriteria selesai:** siklus tiket bisa diuji dari dibuat sampai ditutup dan semua transisi tercatat.

### P2 — Tambahkan intent classification dan klarifikasi

1. Buat modul terpisah untuk 14 taksonomi intent yang ditentukan tim; jangan mengubah logika chat lama sekaligus.
2. Simpan `intent`, `confidence`, dan `classification_source` pada hasil pemrosesan.
3. Terapkan aturan dokumen analisis:
   - confidence >= 0.75: lanjut ke proses otomatis yang diizinkan;
   - 0.45–0.74: minta klarifikasi, maksimal 2 giliran;
   - < 0.45: arahkan ke handoff manusia.
4. Untuk operasi berisiko atau data pribadi, tetap wajibkan pemeriksaan autentikasi dan persetujuan manusia sesuai kebijakan.
5. Sediakan fallback jika model gagal atau mengembalikan output tidak valid.

**Kriteria selesai:** pengujian untuk ketiga rentang confidence menghasilkan alur yang benar. Nilai confidence dari LLM harus diperlakukan sebagai estimasi, bukan jaminan kebenaran.

### P3 — Implementasikan level jaminan identitas (AL)

1. Tambahkan status sesi `AL0` sebagai default anonim.
2. Untuk FAQ umum, izinkan respons tanpa akses data pribadi.
3. Untuk informasi privat, minta verifikasi OTP simulator dan tingkatkan sesi hanya setelah verifikasi berhasil.
4. Terapkan batas 3 kegagalan OTP, lockout 15 menit, dan eskalasi sesuai dokumen kebutuhan.
5. Jangan meminta atau menyimpan PIN, password, CVV, OTP asli, atau nomor kartu penuh di chat maupun log.

**Kriteria selesai:** endpoint dan UI menolak permintaan data privat sebelum AL yang diwajibkan tercapai; lockout dapat diuji.

### P4 — Perkuat knowledge base / RAG

1. Pertahankan pencarian kata saat ini sebagai fallback selama transisi.
2. Siapkan modul retrieval terpisah agar backend bisa beralih ke embedding tanpa menghapus jalur lama.
3. Setelah infrastruktur siap, tambahkan PostgreSQL + pgvector, metadata sumber, versi dokumen, dan status publikasi.
4. Jawaban FAQ wajib merujuk hanya ke dokumen aktif yang ditemukan. Jika sumber tidak cukup, nyatakan keterbatasan dan tawarkan handoff.
5. Tambahkan uji prompt injection pada isi dokumen dan pertanyaan pengguna; perlakukan dokumen sebagai data, bukan instruksi sistem.

**Kriteria selesai:** setiap jawaban berbasis pengetahuan dapat ditelusuri ke sumber yang digunakan dan sistem tidak mengarang saat tidak ada sumber relevan.

### P5 — Handoff agen manusia dan Copilot

1. Buat skema paket handoff dengan 17 field yang disepakati tim. Dokumen kebutuhan memberi contoh beberapa field, tetapi daftar lengkap 17 field perlu ditetapkan bersama tim.
2. Simpan alasan handoff, ringkasan, transkrip, intent, confidence, status AL, prioritas, dan rekomendasi yang relevan.
3. Buat antrean kasus untuk agen manusia dan tampilan detail kasus.
4. Copilot hanya menyarankan draft. Agen manusia harus meninjau dan menekan kirim; jangan mengirim draft AI otomatis.
5. Tambahkan audit untuk penerimaan kasus, perubahan status, edit draft, dan pengiriman balasan.

**Kriteria selesai:** handoff mempertahankan konteks percakapan dan agen bisa meninjau serta membalas tanpa kehilangan riwayat.

### P6 — Upload bukti yang aman

1. Tambahkan endpoint upload dengan whitelist tipe file, batas ukuran, nama file acak, dan pemeriksaan otorisasi tiket.
2. Simpan upload ke area karantina, bukan langsung ke lokasi publik.
3. Integrasikan MinIO dan ClamAV sebagai layanan terpisah setelah konfigurasi lokal stabil.
4. Berkas hanya dapat diakses setelah hasil scan bersih; file terinfeksi ditolak dan tidak dikirim ke LLM.
5. Jangan mengekstrak/menyisipkan teks file mentah ke prompt sebelum diproses dan dibatasi dengan aman.

**Kriteria selesai:** file bersih dan terinfeksi melalui jalur yang berbeda; file karantina tidak dapat diunduh oleh pengguna yang tidak berwenang.

### P7 — SLA, antrean, dan analitik

1. Tentukan kalender kerja, hari libur, zona waktu `Asia/Jakarta`, jam mulai/selesai, dan aturan pause/resume SLA.
2. Tambahkan target SLA per prioritas, waktu respons pertama, waktu penyelesaian, serta alasan pause.
3. Setelah proses sinkron sudah stabil, pindahkan tugas lambat ke Redis/Celery secara bertahap.
4. Tambahkan metrik: jumlah tiket, tiket terbuka, pelanggaran SLA, waktu respons/penyelesaian, alasan handoff, dan hasil QA sampling.
5. Jangan menampilkan angka analitik yang belum dihitung dari data aktual.

**Kriteria selesai:** hasil SLA dapat diuji pada akhir pekan, di luar jam kerja, dan pada tiket yang menunggu pelanggan.

### P8 — Containerization dan kesiapan deployment

Dokumen analisis memilih Docker Compose dengan FastAPI, PostgreSQL 16 + pgvector, Redis/Celery, MinIO, dan ClamAV. Project saat ini belum menggunakan stack tersebut. Terapkan setelah fitur inti dan tes baseline stabil.

1. Tambahkan Dockerfile backend dan compose file secara terpisah; jangan hapus cara menjalankan lokal yang sekarang.
2. Tambahkan layanan satu per satu, mulai dari backend dan database, lalu Redis, MinIO, dan ClamAV.
3. Gunakan healthcheck, volume persisten, jaringan internal, serta konfigurasi melalui environment variables.
4. Jangan expose port database, Redis, MinIO, atau ClamAV ke internet tanpa kebutuhan yang jelas.
5. Tambahkan migrasi database dan prosedur backup/restore sebelum perpindahan dari SQLite.
6. Lakukan review keamanan, pengujian regresi, dan uji beban sebelum deployment.

**Kriteria selesai:** instruksi lokal lama masih terdokumentasi, compose dapat dibangun dari lingkungan bersih, dan data tetap ada setelah container direstart.

## Pembagian kerja tim (mengikuti dokumen analisis)

| Anggota | Fokus urutan awal |
|---|---|
| Anggota 1 — AI & Security Lead | P0 keamanan AI, P2 intent/clarification, P3 AL/OTP, P4 RAG, P6 scan bukti |
| Anggota 2 — Backend Ops & Core API Lead | P0 baseline, P1 state machine/audit, P3 penyimpanan status AL, P7 SLA/queue, migrasi database |
| Anggota 3 — Frontend & Agent Workspace Lead | P0 regresi UI, P1 tampilan status tiket, P3 UI OTP, P5 workspace/handoff/Copilot, P7 dashboard |

## Catatan perbedaan stack

Project yang diberikan menggunakan **FastAPI + SQLite + frontend HTML/JavaScript + Gemini**. Dokumen analisis menargetkan **FastAPI + React/TypeScript + PostgreSQL/pgvector + Redis/Celery + MinIO + ClamAV**. Ini bukan berarti project sekarang harus dibuang. Pertahankan aplikasi yang ada sebagai baseline, lalu migrasikan atau tambahkan komponen secara bertahap setelah setiap tahap lulus tes.

## Definition of Done untuk setiap penambahan

- Ada kebutuhan dan kriteria penerimaan yang jelas.
- Tidak merusak fitur lama (regression test).
- Endpoint memvalidasi input, autentikasi, otorisasi, dan error.
- Aktivitas sensitif dicatat di audit tanpa menyimpan rahasia.
- Panduan menjalankan dan konfigurasi diperbarui.
- Fitur diuji pada skenario berhasil, gagal, dan akses tidak sah.
