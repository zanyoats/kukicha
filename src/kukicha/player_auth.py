from __future__ import annotations

from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .player_config import PlayerAuthOptions


def password_hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hash_password(password: str) -> str:
    return password_hasher().hash(password)


def read_password_hash(auth: PlayerAuthOptions) -> str:
    return auth.password_hash_file.read_text(encoding="utf-8").strip()


def verify_password(auth: PlayerAuthOptions, password: str) -> bool:
    try:
        return password_hasher().verify(read_password_hash(auth), password)
    except (InvalidHashError, VerificationError, VerifyMismatchError, OSError):
        return False


def signed_auth_cookie(auth: PlayerAuthOptions) -> str:
    return auth_cookie_serializer(auth).dumps({"username": auth.username})


def verify_auth_cookie(auth: PlayerAuthOptions, value: str | None) -> bool:
    if not value:
        return False
    try:
        payload = auth_cookie_serializer(auth).loads(
            value,
            max_age=auth.cookie_max_age_seconds,
        )
    except (BadSignature, SignatureExpired, OSError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("username") == auth.username


def auth_cookie_serializer(auth: PlayerAuthOptions) -> URLSafeTimedSerializer:
    password_hash = read_password_hash(auth)
    secret_key = sha256(
        b"kukicha auth cookie\0" + password_hash.encode("utf-8")
    ).hexdigest()
    return URLSafeTimedSerializer(secret_key=secret_key, salt="kukicha-auth-cookie")
