from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ._compat import UTC
from .player_config import PlayerAuthOptions


@dataclass(frozen=True, slots=True)
class AuthCookieDetails:
    username: str
    issued_at: datetime
    expires_at: datetime
    seconds_remaining: int


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
    return auth_cookie_details(auth, value) is not None


def auth_cookie_details(
    auth: PlayerAuthOptions,
    value: str | None,
    *,
    now: datetime | None = None,
) -> AuthCookieDetails | None:
    if not value:
        return None
    try:
        payload, issued_at = auth_cookie_serializer(auth).loads(
            value,
            max_age=auth.cookie_max_age_seconds,
            return_timestamp=True,
        )
    except (BadSignature, SignatureExpired, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    username = payload.get("username")
    if username != auth.username:
        return None
    issued_at = issued_at.astimezone(UTC)
    expires_at = issued_at + timedelta(seconds=auth.cookie_max_age_seconds)
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    seconds_remaining = max(0, int((expires_at - now_utc).total_seconds()))
    return AuthCookieDetails(
        username=str(username),
        issued_at=issued_at,
        expires_at=expires_at,
        seconds_remaining=seconds_remaining,
    )


def auth_cookie_serializer(auth: PlayerAuthOptions) -> URLSafeTimedSerializer:
    password_hash = read_password_hash(auth)
    secret_key = sha256(
        b"kukicha auth cookie\0" + password_hash.encode("utf-8")
    ).hexdigest()
    return URLSafeTimedSerializer(secret_key=secret_key, salt="kukicha-auth-cookie")
