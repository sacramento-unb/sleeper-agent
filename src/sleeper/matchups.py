"""Matchup parsing and head-to-head comparison helpers."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from sleeper.models import Matchup


def find_opponent_matchup(matchups: List[Matchup], roster_or_id: Any) -> Optional[Matchup]:
    """Finds the opposing team's matchup object for the given roster_id, Matchup, or Roster."""
    if isinstance(roster_or_id, Matchup):
        target_roster_id = roster_or_id.roster_id
    elif hasattr(roster_or_id, "roster_id"):
        target_roster_id = getattr(roster_or_id, "roster_id")
    else:
        try:
            target_roster_id = int(roster_or_id)
        except (ValueError, TypeError):
            return None

    user_m = next((m for m in matchups if m.roster_id == target_roster_id), None)
    if not user_m or not user_m.matchup_id:
        return None

    # Opponent shares the same matchup_id
    for m in matchups:
        if m.matchup_id == user_m.matchup_id and m.roster_id != target_roster_id:
            return m
    return None


def get_matchup_pairs(matchups: List[Matchup]) -> List[Tuple[Matchup, Optional[Matchup]]]:
    """Groups matchups into head-to-head pairs by matchup_id."""
    grouped: Dict[int, List[Matchup]] = {}
    for m in matchups:
        if not m.matchup_id:
            continue
        grouped.setdefault(m.matchup_id, []).append(m)

    pairs = []
    for m_list in grouped.values():
        if len(m_list) == 2:
            pairs.append((m_list[0], m_list[1]))
        elif len(m_list) == 1:
            pairs.append((m_list[0], None))
    return pairs
