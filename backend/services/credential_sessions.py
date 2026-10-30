"""Temporary, server-side database credential sessions.

A user types the database password ONCE ("connect"). The server checks it,
keeps it in memory for a limited time, and hands the browser an unguessable
token in an HttpOnly cookie. Scans and table previews then use the stored
password instead of asking again. Disconnecting, or the time limit, erases it.

What this module guarantees
---------------------------
* The password exists only in this process's memory. It is never written to
  PostgreSQL, a file, a log, or an API response, and it is wrapped so that
  repr(), str(), logging and pickling show "<redacted>" instead of the value.
* The token is 256 random bits. Only its SHA-256 is used as the store key, so
  a dump of the store does not reveal a usable token.
* A session is bound to ONE registered source and to an "owner" (the signed-in
  user, once the application has user accounts - see
  routers.catalog.current_session_owner). Presenting it for another source or
  as another owner finds nothing; the caller cannot tell why.
* Two clocks end a session: an idle timeout (renewed by use) and an absolute
  maximum lifetime (never renewed). Both are configurable, see SessionSettings.

What it does NOT do
-------------------
* Memory is per process. With several workers each would have its own store, so
  a request could land on a worker that has never heard of the session. The
  application therefore refuses to create sessions when it can see that more
  than one worker is configured (multi_worker_configured). Running several
  workers needs a shared store (for example Redis holding ciphertext); that is
  deliberately not introduced here.
* A restart of the backend ends every session (users simply reconnect).
* Python cannot reliably wipe a string from memory; deletion drops the last
  reference and relies on garbage collection.
"""

import hashlib
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from uuid import UUID

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 30 * 60  # no use for 30 minutes ends the session
DEFAULT_MAX_SECONDS = 8 * 60 * 60  # and no session outlives 8 hours
DEFAULT_MAX_SESSIONS = 200
_MIN_SECONDS = 60
_MAX_SECONDS = 24 * 60 * 60


class SessionLimitError(Exception):
    """Too many sessions are active at once."""


class _Secret:
    """Holds a secret string. Cannot be printed, logged or pickled by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __reduce__(self):  # pickle / copy
        raise TypeError("a credential secret cannot be serialised")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("immutable")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        logger.warning("%s is not a whole number; using %s", name, default)
        return default


@dataclass(frozen=True)
class SessionSettings:
    """All tunable, read from the environment when the store is created.

    CATALOG_SESSION_IDLE_SECONDS   idle timeout, default 1800, 60..86400
    CATALOG_SESSION_MAX_SECONDS    absolute lifetime, default 28800, 60..86400
    CATALOG_SESSION_MAX_COUNT      most sessions at once, default 200
    CATALOG_SESSION_COOKIE_SAMESITE lax (default) | strict | none
    CATALOG_SESSION_COOKIE_SECURE  true | false (default: secure on https only)
    """

    idle_seconds: int = DEFAULT_IDLE_SECONDS
    max_seconds: int = DEFAULT_MAX_SECONDS
    max_sessions: int = DEFAULT_MAX_SESSIONS
    cookie_samesite: str = "lax"
    cookie_secure: Optional[bool] = None  # None = follow the request scheme

    @classmethod
    def from_env(cls) -> "SessionSettings":
        idle = _env_int("CATALOG_SESSION_IDLE_SECONDS", DEFAULT_IDLE_SECONDS, _MIN_SECONDS, _MAX_SECONDS)
        maximum = _env_int("CATALOG_SESSION_MAX_SECONDS", DEFAULT_MAX_SECONDS, _MIN_SECONDS, _MAX_SECONDS)
        samesite = (os.getenv("CATALOG_SESSION_COOKIE_SAMESITE") or "lax").strip().lower()
        if samesite not in ("lax", "strict", "none"):
            samesite = "lax"
        secure_raw = (os.getenv("CATALOG_SESSION_COOKIE_SECURE") or "").strip().lower()
        secure = True if secure_raw in ("1", "true", "yes") else False if secure_raw in ("0", "false", "no") else None
        return cls(
            idle_seconds=min(idle, maximum),
            max_seconds=maximum,
            max_sessions=_env_int("CATALOG_SESSION_MAX_COUNT", DEFAULT_MAX_SESSIONS, 1, 10_000),
            cookie_samesite=samesite,
            cookie_secure=secure,
        )


def multi_worker_configured() -> bool:
    """True when the environment asks for more than one worker process
    (WEB_CONCURRENCY is what uvicorn and gunicorn both read)."""
    try:
        return int(os.getenv("WEB_CONCURRENCY", "1") or "1") > 1
    except ValueError:
        return False


class ActiveSession:
    """A live session as handed to a caller. The password is only reachable
    through .password; repr() never shows it."""

    __slots__ = ("host", "port", "database", "username", "trusted_connection", "odbc_driver", "_password", "expires_in_seconds")

    def __init__(self, record: "_Record", expires_in_seconds: int) -> None:
        self.host = record.host
        self.port = record.port
        self.database = record.database
        self.username = record.username
        self.trusted_connection = record.trusted_connection
        self.odbc_driver = record.odbc_driver
        self._password = record.password
        self.expires_in_seconds = expires_in_seconds

    @property
    def password(self) -> str:
        return self._password.reveal()

    def __repr__(self) -> str:
        return f"<ActiveSession expires_in={self.expires_in_seconds}s>"


@dataclass
class _Record:
    owner: Optional[str]
    source_id: UUID
    host: str
    port: Optional[int]
    database: str
    username: Optional[str]
    trusted_connection: bool
    odbc_driver: Optional[str]
    password: _Secret
    last_used: float
    hard_expiry: float

    def __repr__(self) -> str:
        return "<session record>"


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CredentialSessionStore:
    def __init__(
        self,
        settings: Optional[SessionSettings] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings or SessionSettings.from_env()
        self._clock = clock
        self._lock = threading.Lock()
        self._records: Dict[str, _Record] = {}

    # -- internals (call with the lock held) --

    def _remaining(self, record: _Record, now: float) -> float:
        return min(
            record.hard_expiry - now,
            record.last_used + self.settings.idle_seconds - now,
        )

    def _purge(self, now: float) -> None:
        for key in [k for k, r in self._records.items() if self._remaining(r, now) <= 0]:
            del self._records[key]

    def _find(self, token: Optional[str], source_id: UUID, owner: Optional[str], now: float) -> Optional[Tuple[str, _Record]]:
        if not token or not isinstance(token, str):
            return None
        key = _key(token)
        record = self._records.get(key)
        if record is None:
            return None
        if self._remaining(record, now) <= 0:
            del self._records[key]
            return None
        if record.source_id != source_id or record.owner != owner:
            return None  # same answer as "unknown": reveal nothing
        return key, record

    # -- public --

    def create(
        self,
        owner: Optional[str],
        source_id: UUID,
        host: str,
        port: Optional[int],
        database: str,
        username: Optional[str],
        trusted_connection: bool,
        odbc_driver: Optional[str],
        password: str,
    ) -> Tuple[str, ActiveSession]:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._records) >= self.settings.max_sessions:
                raise SessionLimitError("too many active sessions")
            record = _Record(
                owner=owner, source_id=source_id, host=host, port=port, database=database,
                username=username, trusted_connection=trusted_connection, odbc_driver=odbc_driver,
                password=_Secret(password or ""), last_used=now,
                hard_expiry=now + self.settings.max_seconds,
            )
            self._records[_key(token)] = record
            return token, ActiveSession(record, int(self._remaining(record, now)))

    def lookup(self, token: Optional[str], source_id: UUID, owner: Optional[str], touch: bool = True) -> Optional[ActiveSession]:
        """The live session for this token, source and owner, or None.
        `touch` renews the idle timeout (use it for real work, not for status)."""
        with self._lock:
            now = self._clock()
            found = self._find(token, source_id, owner, now)
            if found is None:
                return None
            _, record = found
            if touch:
                record.last_used = now
            return ActiveSession(record, int(self._remaining(record, now)))

    def delete(self, token: Optional[str], source_id: UUID, owner: Optional[str]) -> bool:
        with self._lock:
            found = self._find(token, source_id, owner, self._clock())
            if found is None:
                return False
            del self._records[found[0]]
            return True

    def count(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_store: Optional[CredentialSessionStore] = None
_store_lock = threading.Lock()


def get_credential_store() -> CredentialSessionStore:
    """The process-wide store (created on first use from the environment)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = CredentialSessionStore()
        return _store
