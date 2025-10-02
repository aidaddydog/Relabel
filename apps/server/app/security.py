
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from .core.config import settings

_ph = PasswordHasher()

def _pepper() -> str:
    if settings.RELABEL_PEPPER:
        return settings.RELABEL_PEPPER
    if settings.RELABEL_PEPPER_FILE:
        try:
            with open(settings.RELABEL_PEPPER_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "dev-pepper"

def hash_password(pw: str) -> str:
    return _ph.hash(pw + _pepper())

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw + _pepper())
    except VerifyMismatchError:
        return False
    except Exception:
        return False
