import sqlite3
from contextlib import contextmanager
from .config import DATABASE_PATH

@contextmanager
def connect():
    con = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','customer')),
          password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cases (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          subject TEXT NOT NULL DEFAULT 'Percakapan baru', intent TEXT NOT NULL DEFAULT 'Inquiry',
          status TEXT NOT NULL DEFAULT 'Baru', priority TEXT NOT NULL DEFAULT 'Normal',
          risk INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
          sender TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS knowledge (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'FAQ',
          keywords TEXT DEFAULT '', body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Aktif',
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          case_id TEXT, message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_email TEXT NOT NULL, action TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        # Demo accounts only; change passwords and remove this seeding for production.
        from .security import hash_password
        demo = [
          ('admin@vteki.id','Ratna Wulandari','admin','admin123'),
          ('customer@vteki.id','Rina S.','customer','cust1234')
        ]
        for email, name, role, password in demo:
            c.execute('INSERT OR IGNORE INTO users(email,name,role,password_hash) VALUES(?,?,?,?)',
                      (email,name,role,hash_password(password)))
        count = c.execute('SELECT COUNT(*) FROM knowledge').fetchone()[0]
        if count == 0:
            rows = [
             ('Jam layanan kartu kredit','Layanan','jam buka operasional kantor cabang telepon','Layanan kartu kredit tersedia 24 jam lewat aplikasi dan telepon. Kantor cabang buka Senin–Jumat pukul 08.00–17.00 WIB.'),
             ('Biaya tahunan kartu kredit','Biaya','iuran annual fee tarif','Kartu Classic dikenakan biaya tahunan Rp 150.000, kartu Gold Rp 300.000, dan kartu Platinum Rp 600.000. Biaya tahunan dibebaskan di tahun pertama, dan di tahun berikutnya bila total transaksi minimal Rp 12.000.000 per tahun.'),
             ('Cara memblokir kartu hilang','Layanan','blokir kartu dicuri hilang','Kartu yang hilang atau dicuri dapat diblokir lewat aplikasi pada menu Kartu lalu Blokir Sementara, atau lewat layanan telepon 24 jam. Pembukaan blokir memerlukan verifikasi OTP.'),
             ('Bunga dan cicilan kartu kredit','Biaya','bunga cicilan tenor 0%','Bunga kartu kredit sebesar 1,75% per bulan dari saldo yang belum dibayar. Cicilan 0% tersedia untuk tenor 3, 6, dan 12 bulan di merchant rekanan dengan minimal transaksi Rp 1.000.000.'),
             ('Syarat pengajuan kartu kredit','Produk','syarat pengajuan daftar ktp npwp penghasilan','Pemohon berusia minimal 21 tahun, berpenghasilan minimal Rp 3.000.000 per bulan, serta memiliki KTP dan NPWP. Proses persetujuan memerlukan waktu sekitar 7 hari kerja.'),
             ('Kebijakan pengembalian dana','Kebijakan','refund pengembalian dana transaksi gagal','Pengembalian dana dari transaksi ganda atau gagal diproses maksimal 3 hari kerja setelah disetujui supervisor. Mohon siapkan bukti transaksi.')]
            c.executemany('INSERT INTO knowledge(title,category,keywords,body,status) VALUES(?,?,?,?,?)',
                          [(a,b,d,e,'Aktif' if i < 5 else 'Draf') for i,(a,b,d,e) in enumerate(rows)])
