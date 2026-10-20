"""Waiver wire analysis, drop candidate ranking, and FAAB optimization."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sleeper.models import League, Player, Roster
from fantasy.projections import ProjectionEngine
from sleeper.players import get_available_players
from sleeper.rosters import get_faab_remaining


@dataclass
class WaiverClaimTarget:
    player_id: str
    player_name: str
    position: str
    team: Optional[str]
    projected_points: float
    recommended_faab: int
    reason: str
    confidence: float
    priority_score: float = 0.0
    roster_fit: str = ""
    suggested_drop_player_id: Optional[str] = None
    suggested_drop_player_name: Optional[str] = None
    starter_upgrade_pts: float = 0.0
    is_injury_replacement: bool = False
    is_starter_upgrade: bool = False


@dataclass
class DropCandidate:
    player_id: str
    player_name: str
    position: str
    projected_points: float
    reason: str
    drop_score: float = 0.0
    is_surplus: bool = False


@dataclass
class WaiverRecommendation:
    targets: List[WaiverClaimTarget] = field(default_factory=list)
    drop_candidates: List[DropCandidate] = field(default_factory=list)
    faab_remaining: int = 100
    faab_budget: int = 100
    fallbacks: List[WaiverClaimTarget] = field(default_factory=list)


class WaiverOptimizer:
    """Analyzes waiver wire, identifies targets and drop candidates, and recommends bids based on roster needs."""

    def __init__(self, projection_engine: Optional[ProjectionEngine] = None):
        self.projections = projection_engine or ProjectionEngine()

    def analyze(
        self,
        roster: Roster,
        league: League,
        all_players: Dict[str, Player],
        all_rosters: List[Roster],
        top_n: int = 5,
    ) -> WaiverRecommendation:
        self.projections.set_player_database(all_players)
        ppr_bonus = float(league.scoring_settings.get("rec", 0.5))
        is_faab = (league.waiver_type == "faab")
        faab_rem = get_faab_remaining(roster, league) if is_faab else 0
        available = get_available_players(all_players, all_rosters, only_active_nfl=True)

        # 1. Analyze Current Starters
        starters: List[Player] = []
        starters_by_pos: Dict[str, List[Player]] = {}
        inactive_starters_by_pos: Dict[str, List[Player]] = {}
        starter_projs_by_pos: Dict[str, List[float]] = {}

        for pid in roster.starters:
            p = all_players.get(pid)
            if not p:
                continue
            starters.append(p)
            pos = p.position
            starters_by_pos.setdefault(pos, []).append(p)

            proj = self.projections.get_projection(p, ppr_bonus=ppr_bonus)
            pts = 0.0 if p.is_inactive else proj.projected_points
            starter_projs_by_pos.setdefault(pos, []).append(pts)

            if p.is_inactive:
                inactive_starters_by_pos.setdefault(pos, []).append(p)

        # Baseline for FLEX-eligible starters (RB, WR, TE)
        flex_starter_projs = []
        for pos in ("RB", "WR", "TE"):
            flex_starter_projs.extend(starter_projs_by_pos.get(pos, []))
        weakest_flex_proj = min(flex_starter_projs) if flex_starter_projs else 0.0

        # 2. Analyze Bench and Positional Distribution
        bench_players: List[Player] = []
        bench_by_pos: Dict[str, List[Player]] = {}
        healthy_counts: Dict[str, int] = {}
        total_counts: Dict[str, int] = {}

        for pid in roster.players:
            p = all_players.get(pid)
            if not p:
                continue
            total_counts[p.position] = total_counts.get(p.position, 0) + 1
            if not p.is_inactive:
                healthy_counts[p.position] = healthy_counts.get(p.position, 0) + 1

            if pid not in roster.starters:
                bench_players.append(p)
                bench_by_pos.setdefault(p.position, []).append(p)

        # 3. Project and Evaluate Available Candidates based on Roster Needs
        scored_targets: List[WaiverClaimTarget] = []
        for p in available[:80]:  # Top 80 by Sleeper popularity rank
            if not p.is_active_nfl:
                continue
            proj = self.projections.get_projection(p, ppr_bonus=ppr_bonus)
            if proj.projected_points <= 1.0:
                continue

            pos = p.position
            target_proj = proj.projected_points

            # A. Check Injury Replacement
            inactives_at_pos = inactive_starters_by_pos.get(pos, [])
            is_injury_replacement = len(inactives_at_pos) > 0
            inactive_starter_name = inactives_at_pos[0].full_name if is_injury_replacement else ""
            inactive_status = (inactives_at_pos[0].injury_status or "Inativo").upper() if is_injury_replacement else ""

            # B. Check Starter Upgrade
            pos_starter_projs = starter_projs_by_pos.get(pos, [])
            starter_upgrade_pts = 0.0
            is_starter_upgrade = False

            if pos_starter_projs:
                worst_pos_starter_proj = min(pos_starter_projs)
                if target_proj > worst_pos_starter_proj:
                    starter_upgrade_pts = round(target_proj - worst_pos_starter_proj, 1)
                    is_starter_upgrade = True
            elif pos in ("RB", "WR", "TE") and flex_starter_projs:
                if target_proj > weakest_flex_proj:
                    starter_upgrade_pts = round(target_proj - weakest_flex_proj, 1)
                    is_starter_upgrade = True

            # C. Positional Need Multiplier based on Roster Balance
            healthy_at_pos = healthy_counts.get(pos, 0)
            total_at_pos = total_counts.get(pos, 0)

            need_mult = 1.0

            if pos == "RB":
                if healthy_at_pos <= 1:
                    need_mult = 1.60  # Emergency RB room
                elif healthy_at_pos == 2:
                    need_mult = 1.40  # Starters full, but 0 healthy backups
                elif healthy_at_pos == 3:
                    need_mult = 1.20  # Only 1 healthy backup
                elif healthy_at_pos >= 6:
                    need_mult = 0.80  # Already heavily loaded at RB
            elif pos == "WR":
                if healthy_at_pos <= 2:
                    need_mult = 1.45  # Starting WRs thin
                elif healthy_at_pos == 3:
                    need_mult = 1.25
                elif healthy_at_pos >= 6:
                    need_mult = 0.85
            elif pos == "TE":
                if healthy_at_pos == 0:
                    need_mult = 1.60  # No healthy TE on roster!
                elif healthy_at_pos == 1 and not is_starter_upgrade:
                    need_mult = 0.80  # Already have healthy starting TE
                elif healthy_at_pos >= 2 and not is_starter_upgrade:
                    need_mult = 0.50  # 3rd TE is inefficient
            elif pos == "QB":
                if healthy_at_pos == 0:
                    need_mult = 1.80  # No healthy QB!
                elif healthy_at_pos == 1:
                    need_mult = 1.05  # Standard backup need
                elif healthy_at_pos >= 2 and not is_starter_upgrade:
                    need_mult = 0.40  # Carrying 3+ QBs in standard fantasy is inefficient
            elif pos in ("K", "DEF"):
                if healthy_at_pos == 0 or is_injury_replacement:
                    need_mult = 1.30  # Streaming need
                else:
                    need_mult = 0.25  # Never waste waiver priority on 2nd K/DEF if current is active

            # Extra weight if covering an inactive/injured starter
            if is_injury_replacement:
                need_mult += 0.40

            # D. Composite Priority Score
            priority_score = round(
                (target_proj * need_mult)
                + (starter_upgrade_pts * 1.5 if is_starter_upgrade else 0.0)
                + (6.0 if is_injury_replacement else 0.0),
                2
            )

            # E. Roster Fit Description
            fit_parts = []
            if is_injury_replacement:
                fit_parts.append(f"🚨 Cobre desfalque de {inactive_starter_name} ({inactive_status})")
            elif is_starter_upgrade:
                fit_parts.append(f"⚡ Upgrade de titular (+{starter_upgrade_pts:.1f} pts)")
            elif pos in ("RB", "WR") and healthy_at_pos <= 3:
                fit_parts.append(f"🛡️ Reforço de profundidade ({healthy_at_pos} {pos}s ativos)")
            else:
                fit_parts.append(f"📈 Alto teto projetado ({target_proj:.1f} pts)")

            if p.depth_chart_order == 1:
                fit_parts.append("Titular NFL")

            roster_fit = " | ".join(fit_parts)

            # F. FAAB Recommendation
            if is_faab:
                base_pct = 0.03
                if target_proj >= 14.0 or is_injury_replacement:
                    base_pct = 0.22
                elif target_proj >= 11.0 or is_starter_upgrade:
                    base_pct = 0.14
                elif target_proj >= 8.0:
                    base_pct = 0.07

                rec_faab = max(1, int(faab_rem * base_pct * (need_mult / 1.0)))
                rec_faab = min(rec_faab, faab_rem)
            else:
                rec_faab = 0

            reason_str = f"{roster_fit} — {target_proj:.1f} pts proj ({proj.notes})"

            scored_targets.append(
                WaiverClaimTarget(
                    player_id=p.player_id,
                    player_name=p.full_name,
                    position=p.position,
                    team=p.team,
                    projected_points=target_proj,
                    recommended_faab=rec_faab,
                    reason=reason_str,
                    confidence=proj.confidence,
                    priority_score=priority_score,
                    roster_fit=roster_fit,
                    starter_upgrade_pts=starter_upgrade_pts,
                    is_injury_replacement=is_injury_replacement,
                    is_starter_upgrade=is_starter_upgrade,
                )
            )

        # Sort targets by priority_score (balancing roster needs, starter upgrades, and projections)
        scored_targets.sort(key=lambda t: (t.priority_score, t.projected_points), reverse=True)
        top_targets = scored_targets[:top_n]
        fallbacks = scored_targets[top_n : top_n + 5]

        # 4. Evaluate Drop Candidates from Bench with Roster Balance
        drop_candidates: List[DropCandidate] = []
        for bp in bench_players:
            b_proj = self.projections.get_projection(bp, ppr_bonus=ppr_bonus)
            pts = b_proj.projected_points

            reasons = []
            drop_score = 0.0
            is_surplus = False

            pos = bp.position
            total_in_pos = total_counts.get(pos, 0)

            if pos == "QB" and total_in_pos >= 3:
                drop_score += 20.0
                is_surplus = True
                reasons.append("Excesso no elenco (3+ QBs)")
            elif pos in ("K", "DEF") and total_in_pos >= 2:
                drop_score += 25.0
                is_surplus = True
                reasons.append(f"{pos} excedente no banco (não carregar reserva)")
            elif pos == "TE" and total_in_pos >= 3:
                drop_score += 16.0
                is_surplus = True
                reasons.append("3º TE no elenco")
            elif pos in ("WR", "RB") and total_in_pos >= 6:
                drop_score += 8.0
                reasons.append(f"Excedente de profundidade ({total_in_pos} {pos}s no elenco)")

            if bp.is_inactive:
                drop_score += 10.0
                reasons.append(f"Inativo no banco ({bp.injury_status or 'NA'}) sem vaga de IR")
            elif bp.is_injured:
                drop_score += 5.0
                reasons.append(f"Status médico ({bp.injury_status})")

            if pts <= 3.0:
                drop_score += 12.0
                reasons.append(f"Produção mínima ({pts:.1f} pts)")
            elif pts <= 6.0:
                drop_score += 6.0
                reasons.append(f"Baixo teto semanal ({pts:.1f} pts)")

            if bp.depth_chart_order and bp.depth_chart_order >= 3:
                drop_score += 8.0
                reasons.append("Opção secundária/terciária na depth chart da NFL")

            # Protection penalty: do NOT drop the only backup at a thin critical position
            healthy_in_pos = healthy_counts.get(pos, 0)
            if pos in ("RB", "WR") and healthy_in_pos <= 3 and not bp.is_inactive:
                drop_score -= 15.0  # Highly protected

            if not reasons:
                reasons.append(f"Fim de banco ({pts:.1f} pts proj)")

            drop_candidates.append(
                DropCandidate(
                    player_id=bp.player_id,
                    player_name=bp.full_name,
                    position=bp.position,
                    projected_points=pts,
                    reason="; ".join(reasons),
                    drop_score=drop_score,
                    is_surplus=is_surplus,
                )
            )

        drop_candidates.sort(key=lambda d: (d.drop_score, -d.projected_points), reverse=True)

        # 5. Pair Each Target with Suggested Drop Candidate
        used_drop_ids = set()
        for target in top_targets:
            matched_drop = None
            for d in drop_candidates:
                if d.player_id in used_drop_ids:
                    continue
                if d.position == target.position and d.is_surplus:
                    matched_drop = d
                    break

            if not matched_drop:
                for d in drop_candidates:
                    if d.player_id not in used_drop_ids:
                        matched_drop = d
                        break

            if matched_drop:
                used_drop_ids.add(matched_drop.player_id)
                target.suggested_drop_player_id = matched_drop.player_id
                target.suggested_drop_player_name = matched_drop.player_name

        return WaiverRecommendation(
            targets=top_targets,
            drop_candidates=drop_candidates[:top_n],
            faab_remaining=faab_rem if is_faab else 0,
            faab_budget=league.faab_budget if is_faab else 0,
            fallbacks=fallbacks,
        )

