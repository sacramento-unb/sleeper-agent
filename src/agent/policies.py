"""Safety policy enforcement and constraint validation for fantasy actions."""

from __future__ import annotations
from typing import Dict, Optional, Tuple

from sleeper.models import League, Player, Roster
from agent.actions import Action, ActionType


class PolicyViolationError(Exception):
    """Raised when an action violates safety guardrails or league constraints."""
    pass


class SafetyPolicy:
    """Enforces approval gates and valid roster constraints before executing actions."""

    @staticmethod
    def check_approval(action: Action) -> None:
        """Ensures high-impact actions have explicit human approval."""
        if action.requires_approval and not action.approved:
            raise PolicyViolationError(
                f"Action '{action.action_type.value}' ({action.action_id}) requires explicit user approval before execution."
            )

    @staticmethod
    def validate_action(
        action: Action,
        roster: Roster,
        league: League,
        all_players: Dict[str, Player],
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates roster rules and constraints.
        Returns (is_valid, error_message).
        """
        params = action.params

        if action.action_type == ActionType.LINEUP_CHANGE:
            start_pid = str(params.get("start_player_id", ""))
            bench_pid = str(params.get("bench_player_id", ""))

            if start_pid not in roster.players:
                return False, f"Cannot start player {start_pid}: player is not on your roster."

            if bench_pid and bench_pid not in roster.starters:
                return False, f"Cannot bench player {bench_pid}: player is not currently in starting lineup."

            p_start = all_players.get(start_pid)
            if p_start and (p_start.is_inactive or (p_start.injury_status and p_start.injury_status.lower() in ("ir", "out", "na", "pup", "sus"))):
                status_str = p_start.injury_status.upper() if p_start.injury_status else "OUT"
                return False, f"Safety warning: Player {p_start.full_name} is ruled {status_str} (Inactive)."


        elif action.action_type == ActionType.WAIVER_CLAIM:
            add_pid = str(params.get("add_player_id", ""))
            drop_pid = params.get("drop_player_id")
            faab_bid = int(params.get("faab", 0))

            if add_pid in roster.players:
                return False, f"Player {add_pid} is already on your roster."

            if drop_pid:
                drop_pid_str = str(drop_pid)
                if drop_pid_str not in roster.players:
                    return False, f"Cannot drop player {drop_pid}: player is not on your roster."

            # Check FAAB limits
            remaining_faab = max(0, league.faab_budget - roster.faab_spent)
            if faab_bid > remaining_faab:
                return False, f"FAAB bid of ${faab_bid} exceeds remaining budget of ${remaining_faab}."

        elif action.action_type == ActionType.IR_MOVE:
            target_pid = str(params.get("player_id", ""))
            move_direction = params.get("direction", "to_ir")
            p = all_players.get(target_pid)

            if move_direction == "to_ir":
                if not p or not p.injury_status or p.injury_status.lower() not in ("out", "ir", "pup"):
                    return False, f"Player {p.full_name if p else target_pid} is not IR-eligible (status: {p.injury_status if p else 'N/A'})."

        return True, None
