"""Transaction parsing and waiver history tracking."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from sleeper.models import Player, Transaction


def filter_transactions_by_roster(
    transactions: List[Transaction], roster_id: int
) -> List[Transaction]:
    """Filters transactions involving a specific roster."""
    return [t for t in transactions if roster_id in t.roster_ids]


def format_transaction_summary(
    tx: Transaction, all_players: Dict[str, Player]
) -> Dict[str, Any]:
    """Formats a transaction into a clean human/machine readable summary."""
    adds = []
    for pid, rid in tx.adds.items():
        pname = all_players.get(pid).full_name if pid in all_players else f"Player {pid}"
        adds.append({"player_id": pid, "name": pname, "roster_id": rid})

    drops = []
    for pid, rid in tx.drops.items():
        pname = all_players.get(pid).full_name if pid in all_players else f"Player {pid}"
        drops.append({"player_id": pid, "name": pname, "roster_id": rid})

    return {
        "transaction_id": tx.transaction_id,
        "type": tx.type,
        "status": tx.status,
        "roster_ids": tx.roster_ids,
        "adds": adds,
        "drops": drops,
        "faab_bid": tx.faab_bid,
        "created": tx.created,
    }
