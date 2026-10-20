"""Player database query, search, and availability utilities."""

from __future__ import annotations
from typing import Dict, List, Optional, Set
from sleeper.models import Player, Roster


def get_rostered_player_ids(rosters: List[Roster]) -> Set[str]:
    """Returns the set of all player IDs currently on any roster in the league."""
    rostered = set()
    for r in rosters:
        rostered.update(r.players)
    return rostered


def get_available_players(
    all_players: Dict[str, Player],
    rosters: List[Roster],
    position: Optional[str] = None,
    only_active_nfl: bool = True,
) -> List[Player]:
    """
    Identifies all free-agent / waiver players not rostered in the league.
    Sorted by search_rank (popularity/quality).
    """
    rostered = get_rostered_player_ids(rosters)
    available = []

    for pid, p in all_players.items():
        if pid in rostered:
            continue
        if only_active_nfl and not p.is_active_nfl:
            continue
        if position:
            if p.position != position and position not in p.fantasy_positions:
                continue
        available.append(p)

    # Sort primarily by Sleeper search rank (lower is better rank)
    available.sort(key=lambda x: (x.search_rank or 9999, x.full_name))
    return available


def search_players(
    all_players: Dict[str, Player],
    query: str,
    limit: int = 10,
) -> List[Player]:
    """Searches players by name or team."""
    q = query.lower().strip()
    if not q:
        return []

    results = []
    for p in all_players.values():
        if q in p.full_name.lower() or (p.team and q == p.team.lower()):
            results.append(p)

    results.sort(key=lambda x: (x.search_rank or 9999, x.full_name))
    return results[:limit]


def get_player(all_players: Dict[str, Player], player_id: str) -> Player:
    """Returns player object or a fallback unknown player object."""
    if player_id in all_players:
        return all_players[player_id]
    return Player(
        player_id=player_id,
        first_name="Unknown",
        last_name=f"Player ({player_id})",
        position="N/A",
    )
