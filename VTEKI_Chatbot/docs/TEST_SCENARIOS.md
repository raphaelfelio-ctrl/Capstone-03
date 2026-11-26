# Test Scenarios — Baseline dan Pengembangan Berikutnya

Gunakan checklist ini sebelum dan sesudah setiap penambahan. Tandai hasil aktual dan isi bukti/log pengujian sendiri; file ini tidak menyatakan bahwa pengujian sudah dijalankan.

## A. Baseline fitur yang sudah ada

| ID | Skenario | Hasil yang diharapkan |
|---|---|---|
| BASE-01 | Buka `/api/health` | API memberi status layanan dan status konfigurasi Gemini |
| BASE-02 | Login sebagai Customer dengan kredensial demo | Login berhasil dan token dikembalikan |
| BASE-03 | Login dengan password salah | Ditolak; tidak memberikan token |
| BASE-04 | Customer meminta profil `/api/auth/me` dengan token valid | Hanya profil sesi yang dikembalikan |
| BASE-05 | Akses endpoint admin tanpa token | Ditolak dengan 401 |
| BASE-06 | Customer mengakses endpoint khusus admin | Ditolak dengan 403 |
| BASE-07 | Customer mengirim pertanyaan FAQ | Pesan dan jawaban tercatat; jawaban memakai artikel aktif bila cocok |
| BASE-08 | Tanya topik yang tidak ada dalam knowledge base | Sistem mengakui sumber tidak cukup dan/atau mengarahkan ke agen |
| BASE-09 | Customer membuat dan membuka tiket miliknya | Tiket serta riwayat pesan terlihat |
| BASE-10 | Customer mencoba membuka tiket milik pengguna lain | Ditolak |
| BASE-11 | Admin menambah/mengubah artikel knowledge | Perubahan tersimpan dan tercatat |
| BASE-12 | Admin membalas dan menyelesaikan tiket | Status, pesan, notifikasi, dan audit diperbarui |
| BASE-13 | Admin menyetujui/menolak permintaan berisiko demo | Status berubah sesuai keputusan; tidak ada klaim tindakan perbankan nyata |
| BASE-14 | Restart backend | Data SQLite tetap ada |
| BASE-15 | Gemini API gagal/kuota habis | Backend memberi fallback/error yang aman; aplikasi tidak membocorkan key |

## B. Skenario fitur yang perlu ditambahkan sesuai dokumen analisis

| ID | Skenario | Hasil yang diharapkan |
|---|---|---|
| NEXT-01 | Pertanyaan FAQ dari sesi anonim AL0 | FAQ umum dapat dijawab tanpa akses data privat |
| NEXT-02 | Customer meminta status tiket privat saat AL0 | Data tidak ditampilkan; sistem meminta verifikasi |
| NEXT-03 | OTP valid | Level assurance meningkat sesuai aturan yang ditetapkan |
| NEXT-04 | OTP salah 3 kali | Sesi terkunci 15 menit dan diarahkan ke agen manusia |
| NEXT-05 | Intent confidence >= 0.75 | Alur otomatis hanya berjalan untuk tindakan yang diizinkan |
| NEXT-06 | Intent confidence 0.45–0.74 | Klarifikasi dilakukan maksimal dua giliran |
| NEXT-07 | Intent confidence < 0.45 | Handoff manusia dipicu |
| NEXT-08 | Dokumen KB tidak aktif | Tidak dipakai sebagai sumber jawaban |
| NEXT-09 | Pertanyaan mengandung prompt injection | Instruksi pengguna tidak mengesampingkan guardrail; jawaban tetap dibatasi |
| NEXT-10 | Upload file yang diizinkan dan hasil scan bersih | File dipindah ke penyimpanan aman dan hanya dapat diakses pihak berwenang |
| NEXT-11 | Upload file terinfeksi | File ditolak/dikarantina dan tidak diberikan ke LLM |
| NEXT-12 | Handoff dibuat | Paket 17 field lengkap sesuai skema yang disepakati dan konteks tetap terjaga |
| NEXT-13 | Agen menyusun draft Copilot | Draft tidak terkirim sebelum disetujui agen |
| NEXT-14 | Hitung SLA pada akhir pekan/di luar jam kerja | Jam SLA mengikuti kalender kerja `Asia/Jakarta` |
| NEXT-15 | Worker/Redis tidak tersedia | Kegagalan tugas ditangani dan terlihat; tidak diam-diam menghilangkan kasus |
| NEXT-16 | User mencoba mengunduh bukti tiket pengguna lain | Ditolak |
| NEXT-17 | Database/layanan direstart | Data persisten tetap ada setelah prosedur restart normal |
| NEXT-18 | Migrasi database dijalankan pada salinan data | Data lama terverifikasi sebelum aplikasi dialihkan |

## Catatan pelaksanaan

- Jalankan tes pada data demo, bukan data pelanggan sungguhan.
- Catat tanggal, commit/versi, langkah reproduksi, hasil aktual, dan bukti.
- Jangan menempelkan API key, OTP, password, atau data pribadi ke laporan bug.
- Untuk tahap P0, tes baseline harus lulus sebelum mulai mengubah alur utama.
