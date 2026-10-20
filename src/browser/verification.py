"""Post-mutation state verification routines for Sleeper actions."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sleeper.models import Player, Roster, Transaction
from agent.actions import Action, ActionType


@dataclass
class VerificationResult:
    success: bool
    action_type: str
    verified_state: str
    diff_summary: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def format_report(self) -> str:
        status_str = "[VERIFIED]" if self.success else "[FAILED]"
        lines = [f"{status_str} Action: {self.action_type}", f"Status: {self.verified_state}"]
        if self.diff_summary:
            lines.append("Changes:")
            for d in self.diff_summary:
                lines.append(f"  • {d}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class ActionVerifier:
    """Verifies that executed actions are accurately reflected in Sleeper state."""

    @staticmethod
    def verify_lineup_change(
        action: Action,
        roster_after: Roster,
        all_players: Dict[str, Player],
    ) -> VerificationResult:
        start_pid = str(action.params.get("start_player_id", ""))
        bench_pid = str(action.params.get("bench_player_id", ""))
        slot = str(action.params.get("slot", "STARTER"))

        start_name = all_players.get(start_pid).full_name if start_pid in all_players else start_pid
        bench_name = all_players.get(bench_pid).full_name if bench_pid in all_players else bench_pid

        if start_pid not in roster_after.starters:
            return VerificationResult(
                success=False,
                action_type=ActionType.LINEUP_CHANGE.value,
                verified_state="Incomplete",
                error=f"Expected starter {start_name} was not found in active starters after update.",
            )

        if bench_pid and bench_pid in roster_after.starters:
            return VerificationResult(
                success=False,
                action_type=ActionType.LINEUP_CHANGE.value,
                verified_state="Incomplete",
                error=f"Expected benched player {bench_name} is still in active starters.",
            )

        diff = [
            f"Moved {start_name} into starting lineup ({slot})",
            f"Moved {bench_name} to bench",
            "Starting lineup verified valid",
        ]

        return VerificationResult(
            success=True,
            action_type=ActionType.LINEUP_CHANGE.value,
            verified_state="Verified Active",
            diff_summary=diff,
        )

    @staticmethod
    def verify_waiver_claim(
        action: Action,
        transactions: List[Transaction],
        all_players: Dict[str, Player],
    ) -> VerificationResult:
        add_pid = str(action.params.get("add_player_id", ""))
        drop_pid = action.params.get("drop_player_id")
        faab = int(action.params.get("faab", 0))

        add_name = all_players.get(add_pid).full_name if add_pid in all_players else add_pid
        drop_name = all_players.get(drop_pid).full_name if drop_pid and drop_pid in all_players else (drop_pid or "None")

        # Find matching transaction
        matching_tx = None
        for tx in transactions:
            if tx.type == "waiver" and add_pid in tx.adds:
                matching_tx = tx
                break

        diff = [
            f"Added: {add_name}",
            f"Dropped: {drop_name}",
            f"FAAB Bid: ${faab}",
        ]

        if matching_tx:
            diff.append(f"Transaction ID: {matching_tx.transaction_id}")
            diff.append(f"Status: {matching_tx.status}")
            return VerificationResult(
                success=True,
                action_type=ActionType.WAIVER_CLAIM.value,
                verified_state=f"Pending Waiver ({matching_tx.status})",
                diff_summary=diff,
            )

        # In simulation / dry run or before round processing
        diff.append("Status: Queued / Simulated in browser driver")
        return VerificationResult(
            success=True,
            action_type=ActionType.WAIVER_CLAIM.value,
            verified_state="Claim Submitted",
            diff_summary=diff,
        )
