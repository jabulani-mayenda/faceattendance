import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User, UserSession

SESSION_TTL = timedelta(hours=10)
LOGIN_WINDOW_SECONDS = 900
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURES: dict[str, tuple[int, float]] = {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def hash_password(value: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(value: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split('$', 3)
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', value.encode(), base64.urlsafe_b64decode(salt), int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64encode(actual).decode(), expected)
    except Exception:
        return False


def create_session(user: User, db: Session) -> tuple[str, str]:
    token = secrets.token_urlsafe(40)
    csrf_token = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        token_hash=_digest(token),
        csrf_hash=_digest(csrf_token),
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    ))
    db.commit()
    return token, csrf_token


def current_session(request: Request, db: Session) -> UserSession | None:
    token = request.cookies.get('ft_session')
    if not token:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == _digest(token)))
    if not session or session.revoked_at:
        return None
    expiry = session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at
    if expiry <= datetime.now(timezone.utc):
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
        return None
    return session


def current_user(request: Request, db: Session):
    session = current_session(request, db)
    if not session:
        return None
    user = db.get(User, session.user_id)
    return user if user and user.active else None


def require_user(request: Request, db: Session):
    user = current_user(request, db)
    if not user:
        raise HTTPException(401, 'Please sign in')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        session = current_session(request, db)
        csrf_cookie = request.cookies.get('ft_csrf', '')
        csrf_header = request.headers.get('x-csrf-token', '')
        if (not session or not csrf_cookie or not csrf_header
                or not hmac.compare_digest(csrf_cookie, csrf_header)
                or not hmac.compare_digest(_digest(csrf_cookie), session.csrf_hash)):
            raise HTTPException(403, 'CSRF validation failed')
    return user


def login_locked(key: str) -> bool:
    attempt = LOGIN_FAILURES.get(key)
    if not attempt:
        return False
    count, window_start = attempt
    now = time.monotonic()
    if now - window_start >= LOGIN_WINDOW_SECONDS:
        LOGIN_FAILURES.pop(key, None)
        return False
    return count >= LOGIN_MAX_FAILURES


def record_login_failure(key: str):
    now = time.monotonic()
    count, start = LOGIN_FAILURES.get(key, (0, now))
    if now - start >= LOGIN_WINDOW_SECONDS:
        count, start = 0, now
    LOGIN_FAILURES[key] = (count + 1, start)


def clear_login_failures(key: str):
    LOGIN_FAILURES.pop(key, None)


def require_roles(user: User, *roles):
    if user.role not in roles and user.role != 'super_admin':
        raise HTTPException(403, 'You do not have permission to perform this action')
