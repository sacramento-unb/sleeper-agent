"""SQLite database and caching manager for Sleeper Agent."""

from __future__ import annotations
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ADMIN_EMAIL = "sacramento.unb@gmail.com"


def hash_password(password: str) -> Tuple[str, str]:
    """Hash a password using PBKDF2-HMAC-SHA256 with a cryptographically secure salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        100_000,
    )
    return key.hex(), salt


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """Verify a plaintext password against a stored hash and salt."""
    if not hash_hex or not salt_hex:
        return False
    try:
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            100_000,
        )
        return hmac.compare_digest(key.hex(), hash_hex)
    except Exception:
        return False


def get_default_db_path() -> Path:
    """Resolve the default SQLite database path."""
    env_path = os.getenv("SQLITE_DB_PATH")
    if env_path:
        p = Path(env_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # 1. Dedicated host bind-mount directory in container
    if Path("/app/data").is_dir():
        return Path("/app/data/sleeper_agent.db")

    # 2. Local repository ./data directory on host
    local_data = Path(__file__).resolve().parent.parent.parent / "data"
    if local_data.is_dir():
        return local_data / "sleeper_agent.db"

    # 3. Container legacy /app/.cache/sleeper
    if Path("/app/.cache/sleeper").is_dir():
        return Path("/app/.cache/sleeper/sleeper_agent.db")

    # 4. Local fallback (create data dir by default)
    local_data.mkdir(parents=True, exist_ok=True)
    return local_data / "sleeper_agent.db"


class Database:
    """Thread-safe SQLite database manager for user management, sessions, and query caching."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        try:
            os.umask(0)
        except Exception:
            pass
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o777)
            if self.db_path.exists():
                os.chmod(self.db_path, 0o666)
        except Exception:
            pass
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            # 1. Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT,
                    password_salt TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending_password',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. Sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL COLLATE NOCASE,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            # 3. Password Resets table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL COLLATE NOCASE,
                    token TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            # 4. Query Cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    cache_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
            """)

            # Seed default admin if missing
            cursor = conn.execute(
                "SELECT id, status FROM users WHERE email = ? COLLATE NOCASE",
                (ADMIN_EMAIL,),
            )
            admin_row = cursor.fetchone()
            if not admin_row:
                conn.execute(
                    """
                    INSERT INTO users (email, is_admin, status)
                    VALUES (?, 1, 'pending_password');
                    """,
                    (ADMIN_EMAIL,),
                )
                logger.info(f"Initialized default admin account: {ADMIN_EMAIL}")

            # If ADMIN_PASSWORD is set in environment, sync it automatically
            admin_env_pw = os.getenv("ADMIN_PASSWORD")
            if admin_env_pw and len(admin_env_pw.strip()) >= 6:
                pw_hash, salt = hash_password(admin_env_pw.strip())
                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, password_salt = ?, status = 'active', is_admin = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE email = ? COLLATE NOCASE;
                    """,
                    (pw_hash, salt, ADMIN_EMAIL),
                )
                logger.info(f"Synchronized admin password from ADMIN_PASSWORD env var for {ADMIN_EMAIL}")

    # ================= User & Auth Operations =================


    def register_user(self, email: str, is_admin: bool = False) -> Dict[str, Any]:
        """Register a new user email in pending_password state. (Admin only)"""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Endereço de email inválido.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, email, status FROM users WHERE email = ? COLLATE NOCASE",
                (email,),
            )
            existing = cursor.fetchone()
            if existing:
                raise ValueError(f"O email '{email}' já está cadastrado.")

            is_adm_int = 1 if (is_admin or email == ADMIN_EMAIL.lower()) else 0
            cursor = conn.execute(
                """
                INSERT INTO users (email, is_admin, status)
                VALUES (?, ?, 'pending_password');
                """,
                (email, is_adm_int),
            )
            user_id = cursor.lastrowid
            return {
                "id": user_id,
                "email": email,
                "is_admin": bool(is_adm_int),
                "status": "pending_password",
            }

    def set_first_access_password(self, email: str, password: str) -> Dict[str, Any]:
        """Set user password on their first access (when status is pending_password)."""
        email = email.strip().lower()
        if len(password) < 6:
            raise ValueError("A senha deve conter no mínimo 6 caracteres.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, email, is_admin, status FROM users WHERE email = ? COLLATE NOCASE",
                (email,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(
                    f"Acesso negado: o email '{email}' não foi pré-cadastrado pelo administrador. "
                    f"Apenas emails autorizados e previamente cadastrados por {ADMIN_EMAIL} podem acessar o sistema."
                )

            if row["status"] == "active":
                raise ValueError(
                    "Usuário já ativou sua senha anteriormente. Por favor, utilize o Login."
                )

            pw_hash, salt = hash_password(password)
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (pw_hash, salt, row["id"]),
            )

            # Create session immediately for seamless login
            token = self._create_session_internal(
                conn, row["id"], email, bool(row["is_admin"])
            )

            return {
                "id": row["id"],
                "email": email,
                "is_admin": bool(row["is_admin"]),
                "status": "active",
                "session_token": token,
            }

    def authenticate_user(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate user by email and password, returning user info and session token."""
        email = email.strip().lower()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, email, password_hash, password_salt, is_admin, status FROM users WHERE email = ? COLLATE NOCASE",
                (email,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(
                    f"Acesso negado: o email '{email}' não está cadastrado. "
                    "Apenas emails pré-cadastrados pelo administrador têm permissão para acessar a plataforma."
                )

            if row["status"] == "pending_password":
                raise ValueError(
                    "Primeiro acesso pendente: sua conta foi pré-cadastrada pelo administrador, mas a senha ainda não foi definida. "
                    "Por favor, acesse a aba 'Primeiro Acesso' para criar sua senha inicial."
                )

            if not row["password_hash"] or not verify_password(password, row["password_hash"], row["password_salt"]):
                raise ValueError("Credenciais inválidas. Verifique sua senha.")

            token = self._create_session_internal(
                conn, row["id"], email, bool(row["is_admin"])
            )

            return {
                "id": row["id"],
                "email": email,
                "is_admin": bool(row["is_admin"]),
                "status": row["status"],
                "session_token": token,
            }

    def _create_session_internal(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        email: str,
        is_admin: bool,
        duration_hours: int = 72,
    ) -> str:
        token = secrets.token_hex(32)
        now = time.time()
        expires = now + (duration_hours * 3600)
        conn.execute(
            """
            INSERT INTO sessions (token, user_id, email, is_admin, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (token, user_id, email, 1 if is_admin else 0, now, expires),
        )
        return token

    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate and return session details, ensuring the user is still active in database."""
        if not token:
            return None
        with self._lock, self._get_connection() as conn:
            now = time.time()
            cursor = conn.execute(
                """
                SELECT s.token, s.user_id, s.email, s.is_admin, s.expires_at
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > ? AND u.status = 'active';
                """,
                (token, now),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "token": row["token"],
                "user_id": row["user_id"],
                "email": row["email"],
                "is_admin": bool(row["is_admin"]),
                "expires_at": row["expires_at"],
            }

    def invalidate_session(self, token: str) -> None:
        """Invalidate/delete a session on logout."""
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def create_password_reset_token(
        self, email: str, expires_hours: int = 24
    ) -> str:
        """Create a password reset token for a user. (Admin only)"""
        email = email.strip().lower()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, email FROM users WHERE email = ? COLLATE NOCASE",
                (email,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Usuário '{email}' não encontrado.")

            token = secrets.token_hex(16)
            now = time.time()
            expires = now + (expires_hours * 3600)

            # Invalidate any older unused reset tokens for this user
            conn.execute(
                "UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0",
                (row["id"],),
            )

            conn.execute(
                """
                INSERT INTO password_resets (user_id, email, token, expires_at, used)
                VALUES (?, ?, ?, ?, 0);
                """,
                (row["id"], email, token, expires),
            )

            conn.execute(
                "UPDATE users SET status = 'reset_required' WHERE id = ?",
                (row["id"],),
            )

            return token

    def reset_password_with_token(self, token: str, new_password: str) -> bool:
        """Reset password using a valid reset token."""
        token = token.strip()
        if len(new_password) < 6:
            raise ValueError("A nova senha deve conter no mínimo 6 caracteres.")

        with self._lock, self._get_connection() as conn:
            now = time.time()
            cursor = conn.execute(
                """
                SELECT id, user_id, email
                FROM password_resets
                WHERE token = ? AND used = 0 AND expires_at > ?;
                """,
                (token, now),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Token de redefinição inválido ou expirado.")

            pw_hash, salt = hash_password(new_password)
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (pw_hash, salt, row["user_id"]),
            )

            # Mark token as used
            conn.execute(
                "UPDATE password_resets SET used = 1 WHERE id = ?",
                (row["id"],),
            )

            # Invalidate all prior sessions for security
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ?",
                (row["user_id"],),
            )

            return True

    def admin_reset_user_password(
        self, target_email: str, new_password: str
    ) -> bool:
        """Directly set a new password for a user. (Admin only)"""
        target_email = target_email.strip().lower()
        if len(new_password) < 6:
            raise ValueError("A nova senha deve conter no mínimo 6 caracteres.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM users WHERE email = ? COLLATE NOCASE",
                (target_email,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Usuário '{target_email}' não encontrado.")

            pw_hash, salt = hash_password(new_password)
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (pw_hash, salt, row["id"]),
            )

            # Invalidate all prior sessions
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ?",
                (row["id"],),
            )

            return True

    def set_admin_password(self, password: str) -> None:
        """Force set the administrator password and mark active."""
        if len(password) < 6:
            raise ValueError("A senha deve conter no mínimo 6 caracteres.")
        pw_hash, salt = hash_password(password)
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, status = 'active', is_admin = 1, updated_at = CURRENT_TIMESTAMP
                WHERE email = ? COLLATE NOCASE;
                """,
                (pw_hash, salt, ADMIN_EMAIL),
            )
            conn.execute(
                "DELETE FROM sessions WHERE email = ? COLLATE NOCASE;",
                (ADMIN_EMAIL,),
            )
            logger.info(f"Administrator password updated for {ADMIN_EMAIL}")

    def reset_admin_to_pending(self) -> None:
        """Reset administrator to pending_password so admin can create password on first-access."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET password_hash = NULL, password_salt = NULL, status = 'pending_password', is_admin = 1, updated_at = CURRENT_TIMESTAMP
                WHERE email = ? COLLATE NOCASE;
                """,
                (ADMIN_EMAIL,),
            )
            conn.execute(
                "DELETE FROM sessions WHERE email = ? COLLATE NOCASE;",
                (ADMIN_EMAIL,),
            )
            logger.info(f"Administrator {ADMIN_EMAIL} reset to pending_password state")

    def list_users(self) -> List[Dict[str, Any]]:

        """List all registered users. (Admin only)"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, email, is_admin, status, created_at, updated_at
                FROM users
                ORDER BY is_admin DESC, created_at ASC;
                """
            )
            return [
                {
                    "id": r["id"],
                    "email": r["email"],
                    "is_admin": bool(r["is_admin"]),
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in cursor.fetchall()
            ]

    # ================= Query Caching Operations =================

    def get_cached_query(self, cache_key: str) -> Optional[Any]:
        """Retrieve cached query response if valid and not expired."""
        with self._lock, self._get_connection() as conn:
            now = time.time()
            cursor = conn.execute(
                """
                SELECT data_json, created_at, expires_at
                FROM query_cache
                WHERE cache_key = ? AND expires_at > ?;
                """,
                (cache_key, now),
            )
            row = cursor.fetchone()
            if row:
                try:
                    data = json.loads(row["data_json"])
                    if isinstance(data, dict):
                        data["cached"] = True
                        data["cache_age_seconds"] = max(0, int(now - row["created_at"]))
                    return data
                except Exception as e:
                    logger.warning(f"Error parsing cached json for key {cache_key}: {e}")
                    return None
            return None

    def set_cached_query(
        self, cache_key: str, data: Any, ttl_seconds: int = 1800
    ) -> None:
        """Store query response JSON in SQLite with TTL."""
        try:
            payload = json.dumps(data)
            now = time.time()
            expires = now + ttl_seconds
            with self._lock, self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO query_cache (cache_key, data_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        data_json = excluded.data_json,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at;
                    """,
                    (cache_key, payload, now, expires),
                )
        except Exception as e:
            logger.error(f"Failed to cache query for {cache_key}: {e}")

    def clear_cache(self, cache_key_prefix: Optional[str] = None) -> int:
        """Clear cached entries, optionally filtered by prefix."""
        with self._lock, self._get_connection() as conn:
            if cache_key_prefix:
                cursor = conn.execute(
                    "DELETE FROM query_cache WHERE cache_key LIKE ?",
                    (f"{cache_key_prefix}%",),
                )
            else:
                cursor = conn.execute("DELETE FROM query_cache;")
            return cursor.rowcount


_db_singleton: Optional[Database] = None


def get_db(db_path: Optional[str | Path] = None) -> Database:
    """Get or create singleton Database instance."""
    global _db_singleton
    if _db_singleton is None or db_path is not None:
        _db_singleton = Database(db_path)
    return _db_singleton
