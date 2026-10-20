"""Structured Action definitions for fantasy team mutations."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional
import uuid


class ActionType(str, Enum):
    LINEUP_CHANGE = "lineup_change"
    WAIVER_CLAIM = "waiver_claim"
    FREE_AGENT_ADD = "free_agent_add"
    DROP_PLAYER = "drop_player"
    IR_MOVE = "ir_move"
    TRADE_PROPOSAL = "trade_proposal"
    TRADE_ACCEPT = "trade_accept"


@dataclass
class Action:
    action_type: ActionType
    reason: str
    confidence: float = 0.8
    requires_approval: bool = True
    action_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    params: Dict[str, Any] = field(default_factory=dict)
    approved: bool = False
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": self.action_type.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "executed": self.executed,
            **self.params,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Action:
        data_copy = dict(data)
        aid = data_copy.pop("action_id", str(uuid.uuid4())[:8])
        atype_str = data_copy.pop("action", "")
        atype = ActionType(atype_str) if atype_str else ActionType.LINEUP_CHANGE
        reason = data_copy.pop("reason", "")
        confidence = float(data_copy.pop("confidence", 0.8))
        requires_approval = bool(data_copy.pop("requires_approval", True))
        approved = bool(data_copy.pop("approved", False))
        executed = bool(data_copy.pop("executed", False))
        return cls(
            action_id=aid,
            action_type=atype,
            reason=reason,
            confidence=confidence,
            requires_approval=requires_approval,
            approved=approved,
            executed=executed,
            params=data_copy,
        )


def LineupChangeAction(
    bench_player_id: str,
    start_player_id: str,
    slot: str,
    reason: str,
    confidence: float = 0.85,
    bench_player_name: str = "",
    start_player_name: str = "",
) -> Action:
    return Action(
        action_type=ActionType.LINEUP_CHANGE,
        reason=reason,
        confidence=confidence,
        requires_approval=True,
        params={
            "bench_player_id": bench_player_id,
            "start_player_id": start_player_id,
            "bench_player_name": bench_player_name,
            "start_player_name": start_player_name,
            "slot": slot,
        },
    )


def WaiverClaimAction(
    add_player_id: str,
    drop_player_id: Optional[str],
    faab: int,
    reason: str,
    confidence: float = 0.82,
    add_player_name: str = "",
    drop_player_name: str = "",
) -> Action:
    return Action(
        action_type=ActionType.WAIVER_CLAIM,
        reason=reason,
        confidence=confidence,
        requires_approval=True,
        params={
            "add_player_id": add_player_id,
            "drop_player_id": drop_player_id,
            "add_player_name": add_player_name,
            "drop_player_name": drop_player_name,
            "faab": faab,
        },
    )


def TradeAction(
    give_player_ids: list[str],
    receive_player_ids: list[str],
    target_roster_id: int,
    reason: str,
    confidence: float = 0.75,
) -> Action:
    return Action(
        action_type=ActionType.TRADE_PROPOSAL,
        reason=reason,
        confidence=confidence,
        requires_approval=True,
        params={
            "give_player_ids": give_player_ids,
            "receive_player_ids": receive_player_ids,
            "target_roster_id": target_roster_id,
        },
    )
