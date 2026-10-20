"""Session and authentication credentials storage for browser automation."""

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages browser session state without exposing raw secrets."""

    def __init__(self, session_path: Optional[str] = None):
        self.session_path = Path(
            session_path or os.getenv("SLEEPER_SESSION_FILE", ".sleeper_session.json")
        )

    def has_session(self) -> bool:
        """Returns True if a non-empty session file exists."""
        if not self.session_path.exists():
            return False
        try:
            with open(self.session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data and (isinstance(data, dict) or isinstance(data, list)))
        except Exception:
            return False

    def save_session(self, storage_state: Dict[str, Any]) -> None:
        """Saves browser storage state (cookies, local storage) with restricted permissions."""
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_path, "w", encoding="utf-8") as f:
            json.dump(storage_state, f)
        
        # Enforce user read/write only permissions (600) on session file
        try:
            self.session_path.chmod(0o600)
        except Exception:
            pass

    def load_session(self) -> Optional[Dict[str, Any]]:
        """Loads session state if available."""
        if not self.has_session():
            return None
        try:
            with open(self.session_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read session file: {e}")
            return None

    def clear_session(self) -> None:
        """Removes the stored session."""
        if self.session_path.exists():
            self.session_path.unlink()
