from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()

ACCESS = "access"
REFRESH = "refresh"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def hash_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(user_id: str, role: str) -> str:
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    return _create_token(user_id, role, ACCESS, lifetime)


def create_refresh_token(user_id: str, role: str) -> str:
    lifetime = timedelta(days=settings.refresh_token_expire_days)
    return _create_token(user_id, role, REFRESH, lifetime)


def _create_token(user_id: str, role: str, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "jti": uuid4().hex,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
