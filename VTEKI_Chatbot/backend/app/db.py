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
        ]
        for email, name, role, password in demo:
            c.execute('INSERT OR IGNORE INTO users(email,name,role,password_hash) VALUES(?,?,?,?)',
                      (email, name, role, hash_password(password)))
