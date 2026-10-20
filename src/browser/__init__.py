"""Browser automation and verification layer for authenticated Sleeper mutations."""

from browser.auth import SessionManager
from browser.sleeper_ui import SleeperBrowserDriver
from browser.verification import ActionVerifier, VerificationResult

__all__ = [
    "SessionManager",
    "SleeperBrowserDriver",
    "ActionVerifier",
    "VerificationResult",
]
