"""League inspection and settings analysis helpers."""

from __future__ import annotations
from typing import Any, Dict, List
from sleeper.models import League


def get_starting_slots(league: League) -> List[str]:
    """Returns starting slot positions in order, excluding BN, IR, and TAXI."""
    bench_slots = {"BN", "IR", "TAXI"}
    return [pos for pos in league.roster_positions if pos not in bench_slots]


def get_bench_count(league: League) -> int:
    return sum(1 for pos in league.roster_positions if pos == "BN")


def get_ir_count(league: League) -> int:
    return sum(1 for pos in league.roster_positions if pos == "IR")


def get_taxi_count(league: League) -> int:
    return sum(1 for pos in league.roster_positions if pos == "TAXI")


def get_ppr_format(league: League) -> str:
    rec_val = float(league.scoring_settings.get("rec", 0.0))
    if rec_val >= 1.0:
        return "Full PPR (1.0)"
    elif rec_val >= 0.5:
        return "Half PPR (0.5)"
    elif rec_val > 0.0:
        return f"Custom PPR ({rec_val})"
    return "Standard (0.0)"


def is_superflex(league: League) -> bool:
    return "SUPER_FLEX" in league.roster_positions


def get_league_summary(league: League) -> Dict[str, Any]:
    starters = get_starting_slots(league)
    return {
        "league_id": league.league_id,
        "name": league.name,
        "season": league.season,
        "total_teams": league.total_rosters,
        "scoring_type": get_ppr_format(league),
        "waiver_type": league.waiver_type,
        "is_faab": (league.waiver_type == "faab"),
        "faab_budget": league.faab_budget,
        "playoff_week_start": league.playoff_week_start,
        "starting_slots": starters,
        "bench_slots": get_bench_count(league),
        "ir_slots": get_ir_count(league),
        "is_superflex": is_superflex(league),
    }
