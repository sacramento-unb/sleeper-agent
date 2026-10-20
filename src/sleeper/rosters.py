"""Roster parsing and management helpers."""

from __future__ import annotations
from typing import Dict, List, Optional
from sleeper.models import League, Roster, User


def find_user_roster(rosters: List[Roster], user_id: str) -> Optional[Roster]:
    """Finds the roster associated with a specific Sleeper user_id."""
    for r in rosters:
        if r.owner_id == user_id:
            return r
        # Check co-owners if present in settings or metadata
        co_owners = r.settings.get("co_owners") or []
        if user_id in co_owners:
            return r
    return None


def build_roster_owner_map(rosters: List[Roster], users: List[User]) -> Dict[int, User]:
    """Maps roster_id to the owning User."""
    user_by_id = {u.user_id: u for u in users}
    result = {}
    for r in rosters:
        if r.owner_id and r.owner_id in user_by_id:
            result[r.roster_id] = user_by_id[r.owner_id]
        else:
            result[r.roster_id] = User(
                user_id=r.owner_id or "",
                username=f"team_{r.roster_id}",
                display_name=f"Team {r.roster_id}",
            )
    return result


def get_faab_remaining(roster: Roster, league: League) -> int:
    """Calculates remaining FAAB budget."""
    spent = roster.faab_spent
    total = league.faab_budget
    return max(0, total - spent)


def get_roster_breakdown(roster: Roster) -> Dict[str, List[str]]:
    """Returns lists of player IDs separated by starters, bench, IR, and taxi."""
    return {
        "starters": list(roster.starters),
        "bench": roster.bench_players,
        "reserve": list(roster.reserve),
        "taxi": list(roster.taxi),
    }
