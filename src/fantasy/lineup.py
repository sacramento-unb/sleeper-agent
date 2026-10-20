"""Lineup optimization and start/sit decision engine."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sleeper.models import League, Player, Roster
from fantasy.projections import ProjectionEngine, PlayerProjection
from sleeper.leagues import get_starting_slots

SLOT_ELIGIBILITY: Dict[str, Set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}


@dataclass
class LineupMove:
    bench_player_id: str
    start_player_id: str
    bench_player_name: str
    start_player_name: str
    slot: str
    projected_diff: float
    reason: str


@dataclass
class LineupRecommendation:
    current_starters: List[str]
    optimal_starters: List[Tuple[str, str]]  # (slot_name, player_id)
    current_projected_total: float
    optimal_projected_total: float
    moves: List[LineupMove] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class LineupOptimizer:
    """Optimizes weekly starting lineup based on eligibility and projected points."""

    def __init__(self, projection_engine: Optional[ProjectionEngine] = None):
        self.projections = projection_engine or ProjectionEngine()

    def _is_eligible(self, player: Player, slot: str) -> bool:
        allowed = SLOT_ELIGIBILITY.get(slot, {slot})
        if player.position in allowed:
            return True
        for p in player.fantasy_positions:
            if p in allowed:
                return True
        return False

    def optimize(
        self,
        roster: Roster,
        league: League,
        all_players: Dict[str, Player],
    ) -> LineupRecommendation:
        self.projections.set_player_database(all_players)
        ppr_bonus = float(league.scoring_settings.get("rec", 0.5))
        starting_slots = get_starting_slots(league)
        available_pids = [p for p in roster.players if p not in roster.reserve]

        # Map players and project
        player_projs: Dict[str, PlayerProjection] = {}
        for pid in available_pids:
            p = all_players.get(pid)
            if p:
                player_projs[pid] = self.projections.get_projection(p, ppr_bonus=ppr_bonus)
            else:
                player_projs[pid] = PlayerProjection(
                    player_id=pid,
                    projected_points=0.0,
                    floor=0.0,
                    ceiling=0.0,
                    confidence=0.5,
                )

        # Warnings for current starters
        warnings = []
        for i, pid in enumerate(roster.starters):
            if not pid or pid == "0":
                slot_name = starting_slots[i] if i < len(starting_slots) else f"Slot {i}"
                warnings.append(f"Empty starter slot: {slot_name}")
                continue

            p = all_players.get(pid)
            if p:
                if p.is_injured:
                    warnings.append(f"Starter {p.full_name} is ruled {p.injury_status or 'OUT'}")
                elif p.is_questionable:
                    warnings.append(f"Starter {p.full_name} is QUESTIONABLE")

        # Calculate current projected total
        current_total = sum(
            player_projs[pid].projected_points
            for pid in roster.starters
            if pid in player_projs
        )

        # Assignment algorithm:
        # Sort slots: strictly positional first (QB, RB, WR, TE, K, DEF), then flex slots
        indexed_slots = list(enumerate(starting_slots))
        strict_slots = [(idx, s) for idx, s in indexed_slots if s not in ("FLEX", "SUPER_FLEX", "WRRB_FLEX", "REC_FLEX")]
        flex_slots = [(idx, s) for idx, s in indexed_slots if s in ("FLEX", "SUPER_FLEX", "WRRB_FLEX", "REC_FLEX")]
        ordered_slots = strict_slots + flex_slots

        # Available pool sorted by projected points descending
        candidate_pool = sorted(
            available_pids,
            key=lambda pid: player_projs[pid].projected_points,
            reverse=True,
        )

        assigned_starters: Dict[int, str] = {}
        used_pids: Set[str] = set()

        for slot_idx, slot_name in ordered_slots:
            best_pid = None
            for pid in candidate_pool:
                if pid in used_pids:
                    continue
                p = all_players.get(pid)
                if p and self._is_eligible(p, slot_name):
                    best_pid = pid
                    break
            if best_pid:
                assigned_starters[slot_idx] = best_pid
                used_pids.add(best_pid)

        # Reconstruct optimal starter list
        optimal_starter_pairs = []
        optimal_total = 0.0
        for i, sname in enumerate(starting_slots):
            pid = assigned_starters.get(i, "")
            optimal_starter_pairs.append((sname, pid))
            if pid and pid in player_projs:
                optimal_total += player_projs[pid].projected_points

        # Identify proposed moves/swaps between current and optimal
        moves = []
        curr_starter_set = set(p for p in roster.starters if p and p != "0")
        optimal_starter_set = set(assigned_starters.values())

        players_to_bench = curr_starter_set - optimal_starter_set
        players_to_start = optimal_starter_set - curr_starter_set

        # Pair up bench and start moves
        bench_sorted = sorted(
            players_to_bench,
            key=lambda pid: player_projs.get(pid, PlayerProjection(pid, 0, 0, 0, 0)).projected_points,
        )
        start_sorted = sorted(
            players_to_start,
            key=lambda pid: player_projs.get(pid, PlayerProjection(pid, 0, 0, 0, 0)).projected_points,
            reverse=True,
        )

        for bench_pid, start_pid in zip(bench_sorted, start_sorted):
            bp = all_players.get(bench_pid)
            sp = all_players.get(start_pid)
            if sp and sp.is_inactive:
                continue

            b_name = bp.full_name if bp else f"Player {bench_pid}"
            s_name = sp.full_name if sp else f"Player {start_pid}"
            start_pts = player_projs[start_pid].projected_points if start_pid in player_projs else 0.0
            bench_pts = player_projs[bench_pid].projected_points if bench_pid in player_projs else 0.0
            diff = start_pts - bench_pts
            if diff <= 0.0 and not (bp and bp.is_injured):
                continue

            
            reason = f"Upgrade: +{diff:.1f} projected pts"
            if bp and bp.is_injured:
                reason = f"Replace injured starter ({bp.injury_status})"

            # Find matching slot
            slot_label = "STARTER"
            for sname, pid in optimal_starter_pairs:
                if pid == start_pid:
                    slot_label = sname
                    break

            moves.append(
                LineupMove(
                    bench_player_id=bench_pid,
                    start_player_id=start_pid,
                    bench_player_name=b_name,
                    start_player_name=s_name,
                    slot=slot_label,
                    projected_diff=round(diff, 2),
                    reason=reason,
                )
            )

        return LineupRecommendation(
            current_starters=roster.starters,
            optimal_starters=optimal_starter_pairs,
            current_projected_total=round(current_total, 2),
            optimal_projected_total=round(optimal_total, 2),
            moves=moves,
            warnings=warnings,
        )
