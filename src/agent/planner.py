"""Weekly Fantasy GM Workflow Planner."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sleeper.client import SleeperClient
from sleeper.models import League, Matchup, Player, Roster, User
from fantasy.lineup import LineupOptimizer, LineupRecommendation
from fantasy.waivers import WaiverOptimizer, WaiverRecommendation
from agent.actions import Action, LineupChangeAction, WaiverClaimAction
from agent.state import AgentState
from sleeper.rosters import find_user_roster, build_roster_owner_map, get_faab_remaining
from sleeper.matchups import find_opponent_matchup


@dataclass
class WorkflowResult:
    day: str
    headline: str
    summary_lines: List[str] = field(default_factory=list)
    proposed_actions: List[Action] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class WeeklyGMPlanner:
    """Orchestrates weekly GM routines and produces actionable recommendations."""

    def __init__(
        self,
        client: SleeperClient,
        league: League,
        user_id: str,
        lineup_optimizer: Optional[LineupOptimizer] = None,
        waiver_optimizer: Optional[WaiverOptimizer] = None,
    ):
        self.client = client
        self.league = league
        self.user_id = user_id
        self.lineup_optimizer = lineup_optimizer or LineupOptimizer()
        self.waiver_optimizer = waiver_optimizer or WaiverOptimizer()

    def run_tuesday_waiver_report(
        self,
        all_players: Dict[str, Player],
        all_rosters: List[Roster],
        top_n: int = 5,
    ) -> WorkflowResult:
        """Tuesday GM Routine: Waiver wire analysis and drop ranking."""
        user_roster = find_user_roster(all_rosters, self.user_id)
        if not user_roster:
            return WorkflowResult(
                day="Tuesday",
                headline="Waiver Wire Preparation",
                summary_lines=["Error: Could not locate user roster in league."],
            )

        rec = self.waiver_optimizer.analyze(
            user_roster, self.league, all_players, all_rosters, top_n=top_n
        )

        lines = [
            f"FAAB Budget Remaining: ${rec.faab_remaining} / ${rec.faab_budget}",
            f"Recommended Targets ({len(rec.targets)}):",
        ]

        proposed_actions = []
        for i, target in enumerate(rec.targets, 1):
            drop_player_id = rec.drop_candidates[i - 1].player_id if i <= len(rec.drop_candidates) else None
            drop_name = rec.drop_candidates[i - 1].player_name if i <= len(rec.drop_candidates) else "None"

            lines.append(
                f"  Priority {i}: ADD {target.player_name} ({target.position}) | DROP {drop_name} | Bid: ${target.recommended_faab}"
            )
            lines.append(f"    Reason: {target.reason}")

            # Create proposed action
            action = WaiverClaimAction(
                add_player_id=target.player_id,
                drop_player_id=drop_player_id,
                faab=target.recommended_faab,
                reason=target.reason,
                confidence=target.confidence,
                add_player_name=target.player_name,
                drop_player_name=drop_name,
            )
            proposed_actions.append(action)

        if rec.fallbacks:
            lines.append("\nFallback Options:")
            for fb in rec.fallbacks:
                lines.append(f"  - {fb.player_name} ({fb.position}) - ${fb.recommended_faab}")

        return WorkflowResult(
            day="Tuesday",
            headline="Tuesday Morning: Waiver Analysis & FAAB Allocation",
            summary_lines=lines,
            proposed_actions=proposed_actions,
            details={"waiver_recommendation": rec},
        )

    def run_thursday_tnf_check(
        self,
        all_players: Dict[str, Player],
        all_rosters: List[Roster],
    ) -> WorkflowResult:
        """Thursday GM Routine: TNF player check and early injury warnings."""
        user_roster = find_user_roster(all_rosters, self.user_id)
        if not user_roster:
            return WorkflowResult(
                day="Thursday",
                headline="TNF Lineup Check",
                summary_lines=["Error: Could not locate user roster."],
            )

        lineup_rec = self.lineup_optimizer.optimize(user_roster, self.league, all_players)

        lines = [
            f"Current Projected Score: {lineup_rec.current_projected_total:.1f} pts",
        ]

        if lineup_rec.warnings:
            lines.append("\nInjury / Status Alerts:")
            for w in lineup_rec.warnings:
                lines.append(f"  [!] {w}")
        else:
            lines.append("\nNo major injury alerts for starting lineup.")

        proposed_actions = []
        if lineup_rec.moves:
            lines.append(f"\nRecommended Lineup Adjustments ({len(lineup_rec.moves)}):")
            for m in lineup_rec.moves:
                lines.append(
                    f"  START {m.start_player_name} -> BENCH {m.bench_player_name} in {m.slot} ({m.reason})"
                )
                action = LineupChangeAction(
                    bench_player_id=m.bench_player_id,
                    start_player_id=m.start_player_id,
                    slot=m.slot,
                    reason=m.reason,
                    bench_player_name=m.bench_player_name,
                    start_player_name=m.start_player_name,
                )
                proposed_actions.append(action)

        return WorkflowResult(
            day="Thursday",
            headline="Thursday Pre-TNF Status & Injury Check",
            summary_lines=lines,
            proposed_actions=proposed_actions,
            details={"lineup_recommendation": lineup_rec},
        )

    def run_sunday_lineup_lock(
        self,
        all_players: Dict[str, Player],
        all_rosters: List[Roster],
        matchups: List[Matchup],
        users: List[User],
    ) -> WorkflowResult:
        """Sunday Morning GM Routine: Active/inactive lock and optimal lineup calculation."""
        user_roster = find_user_roster(all_rosters, self.user_id)
        if not user_roster:
            return WorkflowResult(
                day="Sunday",
                headline="Sunday Morning Lineup Lock",
                summary_lines=["Error: Could not locate user roster."],
            )

        lineup_rec = self.lineup_optimizer.optimize(user_roster, self.league, all_players)

        # Matchup opponent
        opp_m = find_opponent_matchup(matchups, user_roster.roster_id)
        opp_name = "Opponent"
        if opp_m:
            roster_map = build_roster_owner_map(all_rosters, users)
            if opp_m.roster_id in roster_map:
                opp_name = roster_map[opp_m.roster_id].team_name

        lines = [
            f"Upcoming Matchup vs: {opp_name}",
            f"Current Lineup Projection: {lineup_rec.current_projected_total:.1f} pts",
            f"Optimal Lineup Projection: {lineup_rec.optimal_projected_total:.1f} pts",
        ]

        if lineup_rec.warnings:
            lines.append("\nUrgent Inactive / Injury Warnings:")
            for w in lineup_rec.warnings:
                lines.append(f"  [CRITICAL] {w}")

        proposed_actions = []
        if lineup_rec.moves:
            lines.append(f"\nRequired Lineup Swaps Before Kickoff ({len(lineup_rec.moves)}):")
            for m in lineup_rec.moves:
                lines.append(
                    f"  START {m.start_player_name} -> BENCH {m.bench_player_name} [{m.slot}] ({m.reason})"
                )
                action = LineupChangeAction(
                    bench_player_id=m.bench_player_id,
                    start_player_id=m.start_player_id,
                    slot=m.slot,
                    reason=m.reason,
                    bench_player_name=m.bench_player_name,
                    start_player_name=m.start_player_name,
                )
                proposed_actions.append(action)
        else:
            lines.append("\nStarting lineup is already 100% optimal.")

        return WorkflowResult(
            day="Sunday",
            headline="Sunday Morning: Game Day Active/Inactive & Lineup Lock",
            summary_lines=lines,
            proposed_actions=proposed_actions,
            details={"lineup_recommendation": lineup_rec},
        )
