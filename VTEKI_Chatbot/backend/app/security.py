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
        row = c.execute('''SELECT u.id,u.email,u.name,u.role FROM sessions s
                           JOIN users u ON u.id=s.user_id WHERE s.token=?''', (token,)).fetchone()
    if not row:
        raise HTTPException(401, 'Sesi tidak valid. Silakan login kembali.')
    return dict(row)

def admin_only(user=Depends(current_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Fitur ini hanya tersedia untuk Admin.')
    return user

def audit(user_email: str, action: str):
    with connect() as c:
        c.execute('INSERT INTO audit(user_email,action) VALUES(?,?)', (user_email, action[:500]))
