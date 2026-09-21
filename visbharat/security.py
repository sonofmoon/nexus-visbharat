import hashlib
import secrets


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_last4(token: str) -> str:
    return token[-4:] if token else ''


def generate_api_token() -> str:
    return f"vb_{secrets.token_urlsafe(24)}"
