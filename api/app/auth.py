"""Email + password accounts with opaque bearer tokens.

Passwords are hashed with scrypt. Signing in creates a random token; the client
sends it as `Authorization: Bearer <token>`, and only its SHA-256 is stored, so a
leaked database doesn't leak usable sessions. Signing out deletes the session.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionDep
from app.models import AuthSession, User, as_utc, utcnow

SESSION_TTL = timedelta(days=30)
# scrypt cost: ~50ms and 16 MiB per hash.
_N, _R, _P = 2**14, 8, 1
_MAXMEM = 64 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM
    )
    return hmac.compare_digest(candidate.hex(), digest)


# Checked against when the email is unknown, so a miss takes as long as a wrong password
# and response times don't reveal which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def start_session(session: Session, user: User) -> str:
    """Create a session for `user` and return its bearer token. Clears the user's expired sessions."""
    now = utcnow()
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    session.add(AuthSession(user=user, token_hash=_token_hash(token), expires_at=now + SESSION_TTL))
    return token


def end_session(session: Session, token: str) -> None:
    session.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))


_bearer = HTTPBearer(auto_error=False)
BearerToken = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Not signed in", headers={"WWW-Authenticate": "Bearer"})


def current_token(credentials: BearerToken) -> str:
    if credentials is None:
        raise _unauthorized()
    return credentials.credentials


def current_user(session: SessionDep, token: Annotated[str, Depends(current_token)]) -> User:
    auth = session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if auth is None or as_utc(auth.expires_at) <= utcnow():
        raise _unauthorized()
    return auth.user


CurrentUser = Annotated[User, Depends(current_user)]
