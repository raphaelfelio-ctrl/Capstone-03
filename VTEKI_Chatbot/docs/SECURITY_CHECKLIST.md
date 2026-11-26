# Security Checklist — V-TEKI

Checklist ini melengkapi project yang ada tanpa mengubah implementasi saat ini. Centang hanya setelah benar-benar diperiksa.

## Rahasia dan konfigurasi
- [ ] Rotasi API key Gemini bila pernah masuk ke arsip/chat/repository yang dibagikan.
- [ ] `backend/.env` tidak ikut Git atau arsip distribusi.
- [ ] `APP_SECRET` diganti dengan random secret yang kuat dan berbeda per environment.
- [ ] Password demo diganti atau akun demo dinonaktifkan sebelum demo eksternal.
- [ ] API key tidak pernah dikirim ke frontend/browser, log, screenshot, atau laporan.

## Login dan sesi
- [ ] Rate limit login dan perlindungan brute-force diterapkan.
- [ ] Token sesi memiliki masa berlaku dan dapat dicabut.
- [ ] Logout mencabut token server-side.
- [ ] Semua endpoint privat menguji autentikasi dan role.
- [ ] Akses setiap tiket memeriksa kepemilikan atau izin Admin.
- [ ] Respons error tidak membocorkan detail internal.

## Chat dan AI
- [ ] Data privat tidak diberikan sebelum level assurance yang diwajibkan tercapai.
- [ ] Jawaban RAG hanya memakai sumber yang aktif dan relevan.
- [ ] Prompt injection pada pesan dan dokumen diuji.
- [ ] Model tidak boleh mengaku telah memblokir kartu/memindahkan dana jika tindakan hanya simulasi.
- [ ] Output model divalidasi sebelum digunakan untuk mengubah status atau memanggil tool.
- [ ] Draft Copilot harus disetujui agen manusia sebelum dikirim.

## Bukti dan data
- [ ] Validasi tipe, ukuran, nama, dan isi file dilakukan server-side.
- [ ] File masuk karantina sebelum hasil scan dinyatakan bersih.
- [ ] File berbahaya tidak bisa diunduh atau dikirim ke LLM.
- [ ] File dan tiket dilindungi pemeriksaan kepemilikan.
- [ ] Log tidak berisi OTP, password, API key, CVV, atau nomor kartu penuh.
- [ ] Kebijakan retensi dan penghapusan data ditetapkan.

## Deployment
- [ ] HTTPS/TLS aktif pada environment yang diakses melalui jaringan.
- [ ] Database, Redis, MinIO, dan ClamAV tidak diekspos publik tanpa alasan.
- [ ] Volume persisten dan backup diuji.
- [ ] Prosedur restore diuji, bukan hanya backup.
- [ ] Dependency dipindai dan dikunci versinya untuk deployment.
- [ ] Uji regresi, uji otorisasi, dan review keamanan selesai sebelum rilis.
