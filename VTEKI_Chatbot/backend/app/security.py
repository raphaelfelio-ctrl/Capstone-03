import hashlib, hmac, secrets
from fastapi import Depends, HTTPException, Header
from .db import connect
from .config import APP_SECRET

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 180_000).hex()
    return f'{salt}${digest}'

def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split('$', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 180_000).hex()
        return hmac.compare_digest(actual, expected)
    except ValueError:
        return False

def create_token() -> str:
    # Random bearer token; stored server-side so logout can revoke it.
    return secrets.token_urlsafe(36)

def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Silakan login terlebih dahulu.')
    token = authorization[7:].strip()
    with connect() as c:
        row = c.execute('''SELECT u.id,u.email,u.name,u.role,u.guardrail_strikes,u.ai_suspended,u.is_guest,u.staff_role,
                                  s.token AS session_token, s.al2_until, s.al3_until, s.otp_locked_until, s.channel
                           FROM sessions s
                           JOIN users u ON u.id=s.user_id WHERE s.token=?''', (token,)).fetchone()
    if not row:
        raise HTTPException(401, 'Sesi tidak valid. Silakan login kembali.')
    from .identity import session_level
    user = dict(row)
    user['auth_level'] = session_level(user)  # AL1 (login) .. AL3, dihitung ulang tiap permintaan
    return user


def public_user(user: dict) -> dict:
    """Data user yang aman dikirim ke browser (tanpa token sesi)."""
    return {k: v for k, v in user.items() if k not in ('session_token', 'al2_until', 'al3_until', 'otp_locked_until')}

def admin_only(user=Depends(current_user)):
    """Staf: agen manusia & supervisor."""
    if user['role'] != 'admin':
        raise HTTPException(403, 'Fitur ini hanya tersedia untuk Admin.')
    return user


def supervisor_only(user=Depends(current_user)):
    """Supervisor/Admin: knowledge, guardrail, persetujuan, analitik SLA, QA, audit."""
    if user['role'] != 'admin' or (user.get('staff_role') or 'supervisor') != 'supervisor':
        raise HTTPException(403, 'Fitur ini hanya tersedia untuk Supervisor.')
    return user

def audit(user_email: str, action: str):
    with connect() as c:
        c.execute('INSERT INTO audit(user_email,action) VALUES(?,?)', (user_email, action[:500]))
