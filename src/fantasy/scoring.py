"""Fantasy Football scoring calculations."""

from __future__ import annotations
from typing import Dict, Any


def get_default_scoring_rules(format_type: str = "half_ppr") -> Dict[str, float]:
    """Returns baseline standard, half_ppr, or full_ppr scoring settings."""
    rec = 0.5
    if format_type == "full_ppr":
        rec = 1.0
    elif format_type == "standard":
        rec = 0.0

    return {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rec": rec,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fum_lost": -2.0,
        "pass_2pt": 2.0,
        "rush_2pt": 2.0,
        "rec_2pt": 2.0,
        "fgm_0_19": 3.0,
        "fgm_20_29": 3.0,
        "fgm_30_39": 3.0,
        "fgm_40_49": 4.0,
        "fgm_50p": 5.0,
        "xpm": 1.0,
        "def_td": 6.0,
        "def_st_td": 6.0,
        "sack": 1.0,
        "int": 2.0,
        "fum_rec": 2.0,
        "safe": 2.0,
        "blk_kick": 2.0,
        "pts_allow_0": 10.0,
        "pts_allow_1_6": 7.0,
        "pts_allow_7_13": 4.0,
        "pts_allow_14_20": 1.0,
        "pts_allow_21_27": 0.0,
        "pts_allow_28_34": -1.0,
        "pts_allow_35p": -4.0,
    }


def calculate_fantasy_points(stats: Dict[str, Any], scoring_rules: Dict[str, Any]) -> float:
    """Calculates total fantasy points from a player's box score stats against scoring rules."""
    total = 0.0
    for stat_key, stat_value in stats.items():
        if stat_key in scoring_rules:
            try:
                multiplier = float(scoring_rules[stat_key])
                val = float(stat_value)
                total += multiplier * val
            except (ValueError, TypeError):
                continue
    return round(total, 2)
