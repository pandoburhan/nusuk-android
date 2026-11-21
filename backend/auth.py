import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Hardcoded Bcrypt 12-round hash (kept strictly in backend memory, never stored in DB or exposed to frontend)
HARDCODED_PASSWORD_HASH = b"$2a$12$uJf.6X0E/GvlUL6l.IEqreRr/YoTz0/Gd/7jmtNTSAFdJIRO8MBWa"

# Ephemeral per-process signing key in memory: restarting the backend automatically invalidates old tokens
_SIGNING_SECRET = secrets.token_bytes(32)
TOKEN_TTL_SECONDS = 86400  # 24 hours max lifetime in memory

_bearer_scheme = HTTPBearer(auto_error=False)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(val: str) -> bytes:
    padding = "=" * (-len(val) % 4)
    return base64.urlsafe_b64decode(val + padding)


def verify_password(plain_password: str) -> bool:
    """Verify plaintext password against the hardcoded $2a$12$ bcrypt hash."""
    if not plain_password or not isinstance(plain_password, str):
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), HARDCODED_PASSWORD_HASH)
    except Exception:
        return False


def create_access_token(ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    """Create a stateless HMAC-SHA256 signed access token."""
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_hex(12),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig = hmac.new(_SIGNING_SECRET, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_access_token(token: Optional[str]) -> bool:
    """Verify HMAC signature and expiration of a stateless access token."""
    if not token or not isinstance(token, str) or "." not in token:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(_SIGNING_SECRET, payload_b64.encode("ascii"), hashlib.sha256).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            return False
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            return False
        return True
    except Exception:
        return False


def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency that enforces Bearer token authentication without cookies or sessions."""
    if (
        not credentials
        or credentials.scheme.lower() != "bearer"
        or not verify_access_token(credentials.credentials)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Autentikasi diperlukan",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
