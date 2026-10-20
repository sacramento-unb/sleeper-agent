"""Sleeper API HTTP client with disk caching and rate limiting."""

from __future__ import annotations
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request
import urllib.error

from sleeper.models import (
    League,
    Matchup,
    NFLState,
    Player,
    Roster,
    Transaction,
    User,
)

logger = logging.getLogger(__name__)


class SleeperClient:
    """Public read-only client for Sleeper API v1."""

    def __init__(
        self,
        base_url: str = "https://api.sleeper.app/v1",
        cache_dir: Optional[str] = None,
        cache_ttl_hours: int = 24,
    ):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir or os.getenv("SLEEPER_CACHE_DIR", ".cache/sleeper"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_hours * 3600

    def _get(self, endpoint: str, timeout: int = 15, retries: int = 3) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "User-Agent": "SleeperFantasyAgent/1.0 (Python)",
            "Accept": "application/json",
        }

        req = urllib.request.Request(url, headers=headers)
        last_error = None

        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    if response.status == 200:
                        content = response.read().decode("utf-8")
                        return json.loads(content)
                    else:
                        raise RuntimeError(f"HTTP {response.status} from {url}")
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 404:
                    return None
                if e.code == 429:
                    sleep_time = (2 ** attempt) * 2
                    logger.warning(f"Rate limited by Sleeper (429). Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                    continue
                time.sleep(1)
            except Exception as e:
                last_error = e
                time.sleep(1)

        raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_error}")

    def get_user(self, username_or_id: str) -> Optional[User]:
        data = self._get(f"/user/{username_or_id}")
        if not data:
            return None
        return User.from_dict(data)

    def get_user_leagues(self, user_id: str, season: str, sport: str = "nfl") -> List[League]:
        data = self._get(f"/user/{user_id}/leagues/{sport}/{season}")
        if not data or not isinstance(data, list):
            return []
        return [League.from_dict(d) for d in data]

    def get_league(self, league_id: str) -> Optional[League]:
        data = self._get(f"/league/{league_id}")
        if not data:
            return None
        return League.from_dict(data)

    def get_rosters(self, league_id: str) -> List[Roster]:
        data = self._get(f"/league/{league_id}/rosters")
        if not data or not isinstance(data, list):
            return []
        return [Roster.from_dict(d) for d in data]

    def get_league_users(self, league_id: str) -> List[User]:
        data = self._get(f"/league/{league_id}/users")
        if not data or not isinstance(data, list):
            return []
        return [User.from_dict(d) for d in data]

    def get_matchups(self, league_id: str, week: int) -> List[Matchup]:
        data = self._get(f"/league/{league_id}/matchups/{week}")
        if not data or not isinstance(data, list):
            return []
        return [Matchup.from_dict(d, week=week) for d in data]

    def get_transactions(self, league_id: str, round_num: int) -> List[Transaction]:
        data = self._get(f"/league/{league_id}/transactions/{round_num}")
        if not data or not isinstance(data, list):
            return []
        return [Transaction.from_dict(d) for d in data]

    def get_traded_picks(self, league_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/league/{league_id}/traded_picks")
        return data or []

    def get_nfl_state(self, sport: str = "nfl") -> NFLState:
        data = self._get(f"/state/{sport}")
        if not data:
            return NFLState(week=1, season="2024", season_type="regular")
        return NFLState.from_dict(data)

    def get_all_players(self, sport: str = "nfl", force_refresh: bool = False) -> Dict[str, Player]:
        """Fetch all players with local disk caching (24h TTL) to avoid hitting Sleeper too heavily."""
        cache_file = self.cache_dir / f"players_{sport}.json"
        
        # Check cache validity
        if not force_refresh and cache_file.exists():
            mtime = cache_file.stat().st_mtime
            if (time.time() - mtime) < self.cache_ttl_seconds:
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return {pid: Player.from_dict(pid, pdata) for pid, pdata in data.items()}
                except Exception as e:
                    logger.warning(f"Failed to read players cache: {e}. Refetching...")

        logger.info(f"Downloading player database from Sleeper for {sport}...")
        raw_data = self._get(f"/players/{sport}")
        if raw_data and isinstance(raw_data, dict):
            # Cache to disk
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(raw_data, f)
            except Exception as e:
                logger.warning(f"Could not write cache file {cache_file}: {e}")

            return {pid: Player.from_dict(pid, pdata) for pid, pdata in raw_data.items()}

        return {}

    def get_trending_players(
        self,
        sport: str = "nfl",
        trend_type: str = "add",
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        endpoint = f"/players/{sport}/trending/{trend_type}?lookback_hours={lookback_hours}&limit={limit}"
        data = self._get(endpoint)
        return data or []
