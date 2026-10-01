"""Authentication & security utilities (Module 1 foundation).

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library and,
later, JWT signing for access tokens. No credentials are stored at the
application-configuration layer; secrets must come from environment
variables (see backend/.env.example).
"""

import hashlib
import hmac
import os
import secrets

from app.config import DEV_ONLY_JWT_SECRET, settings

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        int(iterations),
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def random_secret_key() -> str:
    """Generates a strong random secret for production .env files."""
    return secrets.token_urlsafe(48)


def insecure_secret() -> bool:
    """True when no access token issued with this secret could be trusted.

    Both the empty default and the historical development placeholder mean
    "no real secret was configured". This is only advisory outside
    production; :func:`app.config.production_config_problems` makes it fatal.
    """
    return not settings.jwt_secret_key or settings.jwt_secret_key == DEV_ONLY_JWT_SECRET