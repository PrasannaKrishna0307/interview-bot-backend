import os
from datetime import datetime, timedelta
from jose import jwt

SECRET_KEY = os.getenv("JWT_SECRET", "change-me")
ALGORITHM = "HS256"

# Debug: Verify JWT_SECRET is loaded
if SECRET_KEY == "change-me":
    print("⚠️ WARNING: JWT_SECRET not found in environment! Using default 'change-me'")
    print("⚠️ This will cause authentication failures!")
else:
    print(f"✅ JWT_SECRET loaded successfully (length: {len(SECRET_KEY)})")


def create_access_token(data: dict, expires_minutes: int = 60 * 24 * 7) -> str:
    payload = data.copy()
    now = datetime.utcnow()
    payload["exp"] = now + timedelta(minutes=expires_minutes)
    payload["iat"] = now
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

