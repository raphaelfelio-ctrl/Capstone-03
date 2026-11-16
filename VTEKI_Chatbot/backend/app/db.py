import sqlite3
from contextlib import contextmanager
from .config import DATABASE_PATH


@contextmanager
def connect():
    con = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _add_column(c, table, column, ddl):
    if column not in _columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def get_meta(c, key, default=None):
    row = c.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(c, key, value):
    c.execute("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


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
        CREATE TABLE IF NOT EXISTS knowledge_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          filename TEXT NOT NULL,
          file_type TEXT NOT NULL DEFAULT 'md',
          sha256 TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'Aktif',
          chunk_count INTEGER NOT NULL DEFAULT 0,
          source_url TEXT DEFAULT '',
          checked_at TEXT DEFAULT '',
          raw_text TEXT NOT NULL DEFAULT '',
          uploaded_by TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
        CREATE TABLE IF NOT EXISTS guardrail_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          case_id TEXT, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
          stage TEXT NOT NULL,            -- input | llm | output | sensitive
          score REAL NOT NULL DEFAULT 0,
          reasons TEXT NOT NULL DEFAULT '[]',
          message TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL DEFAULT '',
          reviewed INTEGER NOT NULL DEFAULT 0,
          reviewed_by TEXT DEFAULT '',
          verdict TEXT DEFAULT '',        -- confirmed | false_positive
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS otp_challenges (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          session_token TEXT NOT NULL, code_hash TEXT NOT NULL, salt TEXT NOT NULL, expires_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        -- CRM: tiket, riwayat status, handoff 17 field, bukti, QA
        CREATE TABLE IF NOT EXISTS tickets (
          id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE, user_id INTEGER NOT NULL,
          intent_code TEXT NOT NULL, category TEXT NOT NULL, agent TEXT DEFAULT '', priority TEXT NOT NULL DEFAULT 'Normal',
          status TEXT NOT NULL DEFAULT 'Baru', sentiment TEXT DEFAULT 'netral', urgency TEXT DEFAULT 'rendah',
          confidence REAL DEFAULT 0, channel TEXT DEFAULT 'web', assigned_to TEXT DEFAULT '',
          sla_target_min INTEGER NOT NULL DEFAULT 960, sla_due_at TEXT, first_response_due_at TEXT, first_response_at TEXT,
          paused_since TEXT, paused_total_min INTEGER NOT NULL DEFAULT 0, resolved_at TEXT, reopened_count INTEGER NOT NULL DEFAULT 0,
          root_cause TEXT DEFAULT '', closure TEXT DEFAULT '{}', resolution_note TEXT DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ticket_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
          from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS handoffs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
          ticket_id TEXT, reason TEXT NOT NULL, package TEXT NOT NULL, created_at TEXT NOT NULL,
          accepted_by TEXT, accepted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Karantina', scanner TEXT DEFAULT '', scan_result TEXT DEFAULT '', storage_key TEXT NOT NULL,
          text_excerpt TEXT DEFAULT '', reviewed_by TEXT DEFAULT '', created_at TEXT NOT NULL, scanned_at TEXT
        );
        CREATE TABLE IF NOT EXISTS qa_reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
          reviewer TEXT NOT NULL, scores TEXT NOT NULL, total REAL NOT NULL, notes TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        -- Kartu DEMO untuk Card Agent (blokir darurat). Fiktif.
        CREATE TABLE IF NOT EXISTS cards (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          card_masked TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Aktif',
          daily_limit INTEGER NOT NULL DEFAULT 10000000, blocked_at TEXT, blocked_reason TEXT DEFAULT ''
        );
        -- Data rekening DEMO (fiktif). Tidak terhubung ke core banking.
        CREATE TABLE IF NOT EXISTS bank_accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          account_number TEXT NOT NULL, product TEXT NOT NULL, holder_name TEXT NOT NULL,
          balance INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'IDR', status TEXT NOT NULL DEFAULT 'Aktif',
          updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS bank_transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES bank_accounts(id) ON DELETE CASCADE,
          posted_at TEXT NOT NULL, description TEXT NOT NULL, amount INTEGER NOT NULL
        );
        ''')

        # --- Migrasi kolom (aman dijalankan berulang) ---
        _add_column(c, 'users', 'guardrail_strikes', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'users', 'ai_suspended', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'cases', 'flagged', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'cases', 'ai_locked', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'cases', 'handoff_reason', "TEXT DEFAULT ''")
        _add_column(c, 'messages', 'meta', "TEXT DEFAULT '{}'")
        _add_column(c, 'knowledge', 'document_id', 'INTEGER REFERENCES knowledge_documents(id) ON DELETE CASCADE')
        _add_column(c, 'knowledge', 'section', "TEXT DEFAULT ''")
        _add_column(c, 'knowledge', 'kind', "TEXT NOT NULL DEFAULT 'fakta'")  # fakta | instruksi | metadata
        _add_column(c, 'knowledge', 'source_url', "TEXT DEFAULT ''")
        _add_column(c, 'knowledge', 'validity', "TEXT DEFAULT 'umum'")       # umum | dinamis
        _add_column(c, 'knowledge', 'checked_at', "TEXT DEFAULT ''")
        _add_column(c, 'knowledge', 'position', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'knowledge', 'rev', 'INTEGER NOT NULL DEFAULT 1')            # naik tiap kali bagian diedit
        _add_column(c, 'knowledge_documents', 'org_name', "TEXT DEFAULT ''")      # organisasi pemilik knowledge
        # Tingkat autentikasi (AL2/AL3) per sesi + faktor verifikasi per user
        _add_column(c, 'sessions', 'al2_until', 'TEXT')
        _add_column(c, 'sessions', 'al3_until', 'TEXT')
        _add_column(c, 'users', 'phone', "TEXT DEFAULT ''")
        _add_column(c, 'users', 'txn_pin_hash', "TEXT DEFAULT ''")
        _add_column(c, 'users', 'pin_failed', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'users', 'pin_locked_until', 'TEXT')
        _add_column(c, 'cases', 'verified_level', 'INTEGER NOT NULL DEFAULT 1')  # AL tertinggi customer di kasus ini
        # Spesifikasi: kanal, intent 14 taksonomi + keyakinan, sentimen/urgensi, loop klarifikasi
        _add_column(c, 'cases', 'channel', "TEXT NOT NULL DEFAULT 'web'")
        _add_column(c, 'cases', 'intent_code', "TEXT DEFAULT ''")
        _add_column(c, 'cases', 'intent_confidence', 'REAL DEFAULT 0')
        _add_column(c, 'cases', 'sentiment', "TEXT DEFAULT 'netral'")
        _add_column(c, 'cases', 'urgency', "TEXT DEFAULT 'rendah'")
        _add_column(c, 'cases', 'clarify_count', 'INTEGER NOT NULL DEFAULT 0')
        # AL0 tamu & penguncian OTP per sesi
        _add_column(c, 'users', 'is_guest', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'users', 'staff_role', "TEXT DEFAULT ''")   # admin: supervisor | agent
        _add_column(c, 'sessions', 'otp_fail_streak', 'INTEGER NOT NULL DEFAULT 0')
        _add_column(c, 'sessions', 'otp_locked_until', 'TEXT')
        _add_column(c, 'sessions', 'channel', "TEXT NOT NULL DEFAULT 'web'")
        _add_column(c, 'otp_challenges', 'target_user_id', 'INTEGER')
        c.execute('CREATE INDEX IF NOT EXISTS idx_tickets_case ON tickets(case_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_doc ON knowledge(document_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_case ON messages(case_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_guard_case ON guardrail_events(case_id)')

        # Artikel lama (demo Bank Nusantara / impor teks satu-blok) dijadikan Draf
        # agar tidak bercampur dengan knowledge baru. Tidak ada data yang dihapus.
        if get_meta(c, 'kb_v2_migrated') is None:
            c.execute("""UPDATE knowledge SET status='Draf', category='Arsip lama',
                         updated_at=CURRENT_TIMESTAMP WHERE document_id IS NULL""")
            set_meta(c, 'kb_v2_migrated', '1')

        # Demo accounts only; change passwords and remove this seeding for production.
        from .security import hash_password
        demo = [
          ('admin@vteki.id', 'Ratna Wulandari', 'admin', 'admin123'),
          ('customer@vteki.id', 'Rina S.', 'customer', 'cust1234'),
          ('agen@vteki.id', 'Dimas Agen CS', 'admin', 'agen1234'),
        ]
        for email, name, role, password in demo:
            c.execute('INSERT OR IGNORE INTO users(email,name,role,password_hash) VALUES(?,?,?,?)',
                      (email, name, role, hash_password(password)))
        # Data demo untuk verifikasi AL2/AL3: nomor HP, PIN transaksi, dan rekening fiktif.
        c.execute("UPDATE users SET staff_role='supervisor' WHERE email='admin@vteki.id' AND staff_role=''")
        c.execute("UPDATE users SET staff_role='agent' WHERE email='agen@vteki.id' AND staff_role=''")
        c.execute("UPDATE users SET staff_role='supervisor' WHERE role='admin' AND staff_role=''")
        from .identity import seed_demo
        seed_demo(c)
