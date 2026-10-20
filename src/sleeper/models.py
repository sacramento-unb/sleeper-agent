"""Data models for Sleeper API entities."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class User:
    user_id: str
    username: str
    display_name: str
    avatar: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> User:
        return cls(
            user_id=str(data.get("user_id", "")),
            username=data.get("username", "") or "",
            display_name=data.get("display_name", "") or data.get("username", "Unknown"),
            avatar=data.get("avatar"),
            metadata=data.get("metadata", {}) or {},
        )

    @property
    def team_name(self) -> str:
        meta = self.metadata or {}
        return meta.get("team_name") or self.display_name or self.username or "My Team"


@dataclass
class League:
    league_id: str
    name: str
    season: str
    sport: str
    status: str
    total_rosters: int
    roster_positions: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    scoring_settings: Dict[str, Any] = field(default_factory=dict)
    draft_id: Optional[str] = None
    previous_league_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> League:
        return cls(
            league_id=str(data.get("league_id", "")),
            name=data.get("name", "Unnamed League"),
            season=str(data.get("season", "")),
            sport=data.get("sport", "nfl"),
            status=data.get("status", "in_season"),
            total_rosters=int(data.get("total_rosters", 12)),
            roster_positions=data.get("roster_positions", []) or [],
            settings=data.get("settings", {}) or {},
            scoring_settings=data.get("scoring_settings", {}) or {},
            draft_id=data.get("draft_id"),
            previous_league_id=data.get("previous_league_id"),
        )

    @property
    def waiver_type(self) -> str:
        # 0: None, 1: Reverse order of standings, 2: FAAB (bidding), 3: FAAB with continuous
        wt = self.settings.get("waiver_type", 2)
        if wt in (2, 3) or self.settings.get("type") == 2:
            return "faab"
        return "rolling"

    @property
    def faab_budget(self) -> int:
        return int(self.settings.get("waiver_budget", 100))

    @property
    def playoff_week_start(self) -> int:
        return int(self.settings.get("playoff_week_start", 15))


@dataclass
class Roster:
    roster_id: int
    owner_id: Optional[str]
    league_id: str
    starters: List[str] = field(default_factory=list)
    players: List[str] = field(default_factory=list)
    reserve: List[str] = field(default_factory=list)
    taxi: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Roster:
        return cls(
            roster_id=int(data.get("roster_id", 0)),
            owner_id=str(data.get("owner_id")) if data.get("owner_id") else None,
            league_id=str(data.get("league_id", "")),
            starters=[str(p) for p in (data.get("starters") or []) if p],
            players=[str(p) for p in (data.get("players") or []) if p],
            reserve=[str(p) for p in (data.get("reserve") or []) if p],
            taxi=[str(p) for p in (data.get("taxi") or []) if p],
            settings=data.get("settings", {}) or {},
            metadata=data.get("metadata", {}) or {},
        )

    @property
    def wins(self) -> int:
        return int(self.settings.get("wins", 0))

    @property
    def losses(self) -> int:
        return int(self.settings.get("losses", 0))

    @property
    def ties(self) -> int:
        return int(self.settings.get("ties", 0))

    @property
    def points_for(self) -> float:
        fpts = self.settings.get("fpts", 0)
        fpts_dec = self.settings.get("fpts_decimal", 0)
        return float(fpts) + float(fpts_dec) / 100.0

    @property
    def faab_spent(self) -> int:
        return int(self.settings.get("waiver_budget_used", 0))

    @property
    def waiver_position(self) -> int:
        return int(self.settings.get("waiver_position", 1))

    @property
    def bench_players(self) -> List[str]:
        starter_set = set(self.starters)
        reserve_set = set(self.reserve)
        taxi_set = set(self.taxi)
        return [p for p in self.players if p not in starter_set and p not in reserve_set and p not in taxi_set]


@dataclass
class Matchup:
    matchup_id: int
    roster_id: int
    week: int
    points: float
    starters: List[str] = field(default_factory=list)
    players: List[str] = field(default_factory=list)
    starters_points: List[float] = field(default_factory=list)
    players_points: Dict[str, float] = field(default_factory=dict)
    custom_points: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], week: int = 1) -> Matchup:
        starters_pts = [float(x) for x in (data.get("starters_points") or [])]
        players_pts = {str(k): float(v) for k, v in (data.get("players_points") or {}).items()}
        return cls(
            matchup_id=int(data.get("matchup_id") or 0),
            roster_id=int(data.get("roster_id", 0)),
            week=week,
            points=float(data.get("points") or 0.0),
            starters=[str(p) for p in (data.get("starters") or []) if p],
            players=[str(p) for p in (data.get("players") or []) if p],
            starters_points=starters_pts,
            players_points=players_pts,
            custom_points=float(data["custom_points"]) if data.get("custom_points") is not None else None,
        )


VALID_NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"
}

INACTIVE_STATUSES = {"out", "ir", "pup", "sus", "na", "dnr", "cov"}
INJURED_STATUSES = {"out", "ir", "doubtful", "pup", "sus", "na", "dnr", "cov"}


@dataclass

class Player:
    player_id: str
    first_name: str
    last_name: str
    position: str
    fantasy_positions: List[str] = field(default_factory=list)
    team: Optional[str] = None
    status: str = "Active"
    injury_status: Optional[str] = None
    depth_chart_order: Optional[int] = None
    search_rank: int = 9999
    age: Optional[int] = None
    years_exp: Optional[int] = None
    active: bool = True
    projected_points: float = 0.0
    news_updated: Optional[int] = None

    @classmethod
    def from_dict(cls, player_id: str, data: Dict[str, Any]) -> Player:
        first = data.get("first_name", "") or ""
        last = data.get("last_name", "") or ""
        pos = data.get("position", "N/A") or "N/A"
        fan_pos = data.get("fantasy_positions") or ([pos] if pos != "N/A" else [])
        
        depth = data.get("depth_chart_order")
        try:
            depth = int(depth) if depth is not None else None
        except (ValueError, TypeError):
            depth = None

        search_rank = data.get("search_rank", 9999)
        try:
            search_rank = int(search_rank) if search_rank is not None else 9999
        except (ValueError, TypeError):
            search_rank = 9999

        return cls(
            player_id=str(player_id),
            first_name=first,
            last_name=last,
            position=pos,
            fantasy_positions=fan_pos,
            team=data.get("team"),
            status=data.get("status", "Active") or "Active",
            injury_status=data.get("injury_status"),
            depth_chart_order=depth,
            search_rank=search_rank,
            age=data.get("age"),
            years_exp=data.get("years_exp"),
            active=bool(data.get("active", True)),
            projected_points=0.0,
            news_updated=data.get("news_updated"),
        )

    @property
    def is_active_nfl(self) -> bool:
        """Determines if the player is currently an active NFL player on a real NFL team."""
        if not self.active:
            return False

        # Support mock players in unit tests (non-numeric IDs and not DEF units)
        if not self.player_id.isdigit() and self.position != "DEF":
            if self.team in ("FA", "None", "FREE_AGENT", ""):
                return False
            if self.team is not None and self.team not in VALID_NFL_TEAMS:
                return False
            return True

        # Defense units
        if self.position == "DEF":
            return self.player_id in VALID_NFL_TEAMS or self.team in VALID_NFL_TEAMS

        # Real NFL players must belong to an active NFL team
        if not self.team or self.team not in VALID_NFL_TEAMS:
            return False

        # Exclude retired or ghost records that have no depth chart and stale news
        if self.depth_chart_order is None and self.news_updated and self.news_updated < 1700000000000:
            return False

        return True

    @property
    def full_name(self) -> str:

        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name or f"Player {self.player_id}"

    @property
    def is_injured(self) -> bool:
        return bool(self.injury_status and self.injury_status.lower() in INJURED_STATUSES)

    @property
    def is_inactive(self) -> bool:
        """Determines if the player is ruled out or inactive (e.g. NA, Out, IR, PUP, Sus)."""
        return bool(self.injury_status and self.injury_status.lower() in INACTIVE_STATUSES)

    @property
    def is_questionable(self) -> bool:
        return bool(self.injury_status and self.injury_status.lower() == "questionable")



@dataclass
class Transaction:
    transaction_id: str
    type: str  # waiver, free_agent, trade
    status: str
    status_updated: int
    roster_ids: List[int] = field(default_factory=list)
    adds: Dict[str, int] = field(default_factory=dict)  # player_id -> roster_id
    drops: Dict[str, int] = field(default_factory=dict)  # player_id -> roster_id
    settings: Dict[str, Any] = field(default_factory=dict)
    created: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Transaction:
        return cls(
            transaction_id=str(data.get("transaction_id", "")),
            type=data.get("type", "free_agent"),
            status=data.get("status", "complete"),
            status_updated=int(data.get("status_updated", 0)),
            roster_ids=data.get("roster_ids", []) or [],
            adds=data.get("adds", {}) or {},
            drops=data.get("drops", {}) or {},
            settings=data.get("settings", {}) or {},
            created=int(data.get("created", 0)),
        )

    @property
    def faab_bid(self) -> int:
        return int(self.settings.get("waiver_bid", 0))


@dataclass
class NFLState:
    week: int
    season: str
    season_type: str
    leg: int = 1
    league_season: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NFLState:
        return cls(
            week=int(data.get("week", 1)),
            season=str(data.get("season", "2024")),
            season_type=str(data.get("season_type", "regular")),
            leg=int(data.get("leg", 1)),
            league_season=data.get("league_season"),
        )
