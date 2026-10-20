"""Agent internal state management and persistence."""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.actions import Action


@dataclass
class AgentState:
    league: Dict[str, Any] = field(default_factory=dict)
    team: Dict[str, Any] = field(default_factory=dict)
    players: Dict[str, Any] = field(default_factory=dict)
    free_agents: List[Dict[str, Any]] = field(default_factory=list)
    matchup: Dict[str, Any] = field(default_factory=dict)
    transactions: List[Dict[str, Any]] = field(default_factory=list)
    injuries: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    pending_actions: List[Action] = field(default_factory=list)
    drop_candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "league": self.league,
            "team": self.team,
            "players": self.players,
            "free_agents": self.free_agents,
            "matchup": self.matchup,
            "transactions": self.transactions,
            "injuries": self.injuries,
            "recommendations": self.recommendations,
            "pending_actions": [a.to_dict() for a in self.pending_actions],
            "drop_candidates": self.drop_candidates,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentState:
        pending = [Action.from_dict(a) for a in data.get("pending_actions", [])]
        return cls(
            league=data.get("league", {}),
            team=data.get("team", {}),
            players=data.get("players", {}),
            free_agents=data.get("free_agents", []),
            matchup=data.get("matchup", {}),
            transactions=data.get("transactions", []),
            injuries=data.get("injuries", []),
            recommendations=data.get("recommendations", []),
            pending_actions=pending,
            drop_candidates=data.get("drop_candidates", []),
        )

    def save(self, filepath: Optional[str] = None) -> None:
        path = Path(filepath or os.getenv("AGENT_STATE_PATH", ".cache/sleeper/agent_state.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: Optional[str] = None) -> AgentState:
        path = Path(filepath or os.getenv("AGENT_STATE_PATH", ".cache/sleeper/agent_state.json"))
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return cls()

    def add_pending_action(self, action: Action) -> None:
        self.pending_actions.append(action)

    def get_action_by_id(self, action_id: str) -> Optional[Action]:
        for a in self.pending_actions:
            if a.action_id == action_id:
                return a
        return None
