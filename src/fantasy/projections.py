"""Player fantasy projection engine with positional ranking and scoring format calibration."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional
from sleeper.models import Player


@dataclass
class PlayerProjection:
    player_id: str
    projected_points: float
    floor: float
    ceiling: float
    confidence: float
    notes: str = ""


class ProjectionEngine:
    """Calculates projected fantasy points, floor, and ceiling for players."""

    def __init__(self, external_projections: Optional[Dict[str, float]] = None):
        self.external_projections = external_projections or {}
        self.positional_ranks: Dict[str, int] = {}

    def set_player_database(self, all_players: Dict[str, Player]) -> None:
        """Computes rank within each position based on search_rank among active NFL players."""
        if len(all_players) < 30:
            self.positional_ranks = {}
            return

        pos_map: Dict[str, list] = {}
        for pid, p in all_players.items():
            if not p.is_active_nfl:
                continue
            pos = p.position
            if pos:
                s_rank = p.search_rank
                if pos == "DEF" and (s_rank is None or s_rank >= 9000):
                    s_rank = 150
                pos_map.setdefault(pos, []).append((s_rank if s_rank is not None else 9999, pid))

        self.positional_ranks = {}
        for pos, player_list in pos_map.items():
            player_list.sort(key=lambda x: (x[0], x[1]))
            for rank_idx, (_, pid) in enumerate(player_list, 1):
                self.positional_ranks[pid] = rank_idx

    def get_projection(self, player: Player, ppr_bonus: float = 0.5) -> PlayerProjection:
        # Check if player is inactive, ruled out (NA, Out, IR, PUP, Sus), or not on active NFL team
        if not player.is_active_nfl or player.is_inactive:
            return PlayerProjection(
                player_id=player.player_id,
                projected_points=0.0,
                floor=0.0,
                ceiling=0.0,
                confidence=1.0,
                notes=f"Player is inactive/ruled out/retired ({player.injury_status or 'Inactive/Free Agent'})",
            )

        # Check explicit external projection
        if player.player_id in self.external_projections:
            base_pts = float(self.external_projections[player.player_id])
            return PlayerProjection(
                player_id=player.player_id,
                projected_points=base_pts,
                floor=round(base_pts * 0.65, 1),
                ceiling=round(base_pts * 1.45, 1),
                confidence=0.9,
                notes="External projection feed",
            )


        pos = player.position
        overall_rank = player.search_rank or 9999
        depth = player.depth_chart_order or 1

        # Determine positional rank
        if player.player_id in self.positional_ranks:
            pos_rank = self.positional_ranks[player.player_id]
        else:
            # Fallback estimation from overall rank
            if pos == "QB":
                pos_rank = max(1, (overall_rank - 15) // 5) if overall_rank < 300 else 30
            elif pos in ("RB", "WR"):
                pos_rank = max(1, overall_rank // 3) if overall_rank < 300 else 60
            elif pos == "TE":
                pos_rank = max(1, (overall_rank - 20) // 8) if overall_rank < 300 else 25
            else:
                pos_rank = max(1, (overall_rank - 120) // 5) if overall_rank < 300 else 20

        # Positional calibration for NFL Half-PPR standard
        if pos == "QB":
            if pos_rank <= 6:
                base = 20.5 - ((pos_rank - 1) * 0.5)
            elif pos_rank <= 12:
                base = 18.0 - ((pos_rank - 7) * 0.4)
            elif pos_rank <= 24:
                base = 15.6 - ((pos_rank - 13) * 0.3)
            elif pos_rank <= 32:
                base = 12.0 - ((pos_rank - 25) * 0.25)
            else:
                base = 8.0 if depth == 1 else 1.0

        elif pos == "RB":
            if pos_rank <= 6:
                base = 16.5 - ((pos_rank - 1) * 0.4) + (ppr_bonus * 2.2)
            elif pos_rank <= 12:
                base = 14.5 - ((pos_rank - 7) * 0.35) + (ppr_bonus * 2.0)
            elif pos_rank <= 24:
                base = 12.4 - ((pos_rank - 13) * 0.25) + (ppr_bonus * 1.6)
            elif pos_rank <= 36:
                base = 9.8 - ((pos_rank - 25) * 0.2) + (ppr_bonus * 1.2)
            elif pos_rank <= 48:
                base = 7.5 - ((pos_rank - 37) * 0.15) + (ppr_bonus * 1.0)
            else:
                base = 4.5 if depth <= 2 else 1.0

        elif pos == "WR":
            if pos_rank <= 6:
                base = 15.5 - ((pos_rank - 1) * 0.4) + (ppr_bonus * 3.0)
            elif pos_rank <= 12:
                base = 13.5 - ((pos_rank - 7) * 0.35) + (ppr_bonus * 2.6)
            elif pos_rank <= 24:
                base = 11.5 - ((pos_rank - 13) * 0.25) + (ppr_bonus * 2.0)
            elif pos_rank <= 36:
                base = 9.2 - ((pos_rank - 25) * 0.2) + (ppr_bonus * 1.5)
            elif pos_rank <= 50:
                base = 7.0 - ((pos_rank - 37) * 0.15) + (ppr_bonus * 1.0)
            else:
                base = 4.0 if depth <= 3 else 1.0

        elif pos == "TE":
            if pos_rank <= 4:
                base = 11.5 - ((pos_rank - 1) * 0.5) + (ppr_bonus * 2.6)
            elif pos_rank <= 10:
                base = 9.5 - ((pos_rank - 5) * 0.35) + (ppr_bonus * 2.0)
            elif pos_rank <= 18:
                base = 7.5 - ((pos_rank - 11) * 0.3) + (ppr_bonus * 1.5)
            elif pos_rank <= 28:
                base = 5.4 - ((pos_rank - 19) * 0.2) + (ppr_bonus * 1.0)
            else:
                base = 3.5 if depth == 1 else 0.5

        elif pos == "K":
            if pos_rank <= 12:
                base = 8.8 - ((pos_rank - 1) * 0.12)
            else:
                base = 7.2

        elif pos == "DEF":
            if pos_rank <= 12:
                base = 8.5 - ((pos_rank - 1) * 0.15)
            else:
                base = 6.5

        else:
            base = 3.0

        # Adjust for injuries
        notes = f"Positional Rank: {pos}#{pos_rank}"
        if player.injury_status:
            stat = player.injury_status.lower()
            if stat == "questionable":
                base *= 0.80
                notes += " (Questionable: -20%)"
            elif stat == "doubtful":
                base *= 0.25
                notes += " (Doubtful: -75%)"

        proj = max(0.0, round(base, 2))
        floor = max(0.0, round(proj * 0.65, 2))
        ceiling = round(proj * 1.45, 2)

        return PlayerProjection(
            player_id=player.player_id,
            projected_points=proj,
            floor=floor,
            ceiling=ceiling,
            confidence=0.85,
            notes=notes,
        )
