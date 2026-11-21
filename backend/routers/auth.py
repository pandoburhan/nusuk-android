from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.auth import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class VerifyPasswordRequest(BaseModel):
    password: str


@router.post("/verify")
def verify_dashboard_password(body: VerifyPasswordRequest):
    """Verify dashboard password against hardcoded Bcrypt hash and issue stateless token."""
    if not verify_password(body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password salah. Silakan coba lagi.",
        )
    token = create_access_token()
    return {
        "ok": True,
        "access_token": token,
        "token_type": "bearer",
    }
