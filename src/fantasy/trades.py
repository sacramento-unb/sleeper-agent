"""Trade evaluation and roster impact analysis with two-team contextual optimization."""

from __future__ import annotations
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Set
import unicodedata

from sleeper.models import League, Player, Roster
from fantasy.projections import ProjectionEngine
from fantasy.lineup import LineupOptimizer


def clean_name(s: str) -> str:
    """Normalize player names for fuzzy comparison."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    s = re.sub(r"[^\w\s]", "", s.lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return " ".join(s.split())


def resolve_player(ident: str, all_players: Dict[str, Player]) -> Player:
    """
    Resolves an input identifier to a known Player.
    Supports:
    1. Direct player_id match
    2. Exact full name match (normalized)
    3. Exact last name match
    4. Substring / partial match
    Prioritizes active NFL players and best search_rank.
    """
    ident_str = str(ident).strip()
    if ident_str in all_players:
        return all_players[ident_str]

    target = clean_name(ident_str)
    if not target:
        return Player(ident_str, "Unknown", ident_str, "N/A")

    # 1. Exact full name match
    exact = [
        p for p in all_players.values()
        if clean_name(p.full_name or f"{p.first_name} {p.last_name}") == target
    ]
    if exact:
        exact.sort(key=lambda p: (not p.is_active_nfl, p.search_rank if p.search_rank is not None else 9999))
        return exact[0]

    # 2. Exact last name match
    lasts = [
        p for p in all_players.values()
        if clean_name(p.last_name or "") == target
    ]
    if lasts:
        lasts.sort(key=lambda p: (not p.is_active_nfl, p.search_rank if p.search_rank is not None else 9999))
        return lasts[0]

    # 3. Substring match
    subs = [
        p for p in all_players.values()
        if target in clean_name(p.full_name or "") or clean_name(p.full_name or "") in target
    ]
    if subs:
        subs.sort(key=lambda p: (not p.is_active_nfl, p.search_rank if p.search_rank is not None else 9999))
        return subs[0]

    return Player(ident_str, "Unknown", ident_str, "N/A")


@dataclass
class TeamTradeImpact:
    roster_id: int
    team_name: str
    owner_name: str
    give_players: List[Player]
    receive_players: List[Player]
    starter_projected_before: float
    starter_projected_after: float
    starter_projected_diff: float
    total_projected_before: float
    total_projected_after: float
    total_projected_diff: float
    positional_impacts: List[str] = field(default_factory=list)
    surpluses: List[str] = field(default_factory=list)
    deficits: List[str] = field(default_factory=list)
    verdict: str = ""
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roster_id": self.roster_id,
            "team_name": self.team_name,
            "owner_name": self.owner_name,
            "starter_projected_before": round(self.starter_projected_before, 1),
            "starter_projected_after": round(self.starter_projected_after, 1),
            "starter_projected_diff": round(self.starter_projected_diff, 1),
            "total_projected_before": round(self.total_projected_before, 1),
            "total_projected_after": round(self.total_projected_after, 1),
            "total_projected_diff": round(self.total_projected_diff, 1),
            "positional_impacts": self.positional_impacts,
            "surpluses": self.surpluses,
            "deficits": self.deficits,
            "verdict": self.verdict,
            "summary": self.summary,
        }


@dataclass
class TradeEvaluation:
    give_players: List[Player]
    receive_players: List[Player]
    give_total_projected: float
    receive_total_projected: float
    net_projected_diff: float
    verdict: str  # "Favorable", "Fair", "Unfavorable", "High Risk", "Win-Win", etc.
    roster_impacts: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    summary: str = ""
    # Two-team contextual analysis fields:
    my_team: Optional[TeamTradeImpact] = None
    partner_team: Optional[TeamTradeImpact] = None
    win_win_score: float = 0.0  # 0 to 100
    feasibility: str = ""
    negotiation_tips: List[str] = field(default_factory=list)

    @property
    def my_team_dict(self) -> Optional[Dict[str, Any]]:
        return self.my_team.to_dict() if self.my_team else None

    @property
    def partner_team_dict(self) -> Optional[Dict[str, Any]]:
        return self.partner_team.to_dict() if self.partner_team else None


class TradeAnalyzer:
    """Evaluates trades by balancing projected points, positional scarcity, and depth impact."""

    def __init__(self, projection_engine: Optional[ProjectionEngine] = None):
        self.projections = projection_engine or ProjectionEngine()
        self.lineup_optimizer = LineupOptimizer(self.projections)

    def evaluate_trade(
        self,
        roster: Roster,
        give_player_ids: List[str],
        receive_player_ids: List[str],
        all_players: Dict[str, Player],
        league: Optional[League] = None,
        partner_roster: Optional[Roster] = None,
        all_rosters: Optional[List[Roster]] = None,
        owner_map: Optional[Dict[int, Any]] = None,
    ) -> TradeEvaluation:
        # Initialize projections database
        self.projections.set_player_database(all_players)
        ppr_bonus = float(league.scoring_settings.get("rec", 0.5)) if league and league.scoring_settings else 0.5

        # Resolve players by ID or Name
        give_players = [resolve_player(pid, all_players) for pid in give_player_ids]
        receive_players = [resolve_player(pid, all_players) for pid in receive_player_ids]

        # Calculate raw projections
        give_proj = sum(self.projections.get_projection(p, ppr_bonus=ppr_bonus).projected_points for p in give_players)
        rec_proj = sum(self.projections.get_projection(p, ppr_bonus=ppr_bonus).projected_points for p in receive_players)
        raw_diff = rec_proj - give_proj

        # Positional counts change
        roster_impacts = []
        for p in give_players:
            roster_impacts.append(f"{p.position} depth: decreases (losing {p.full_name})")
        for p in receive_players:
            roster_impacts.append(f"{p.position} depth: increases (gaining {p.full_name})")

        # Risk assessments
        risks = []
        for p in receive_players:
            if p.is_injured or p.is_inactive:
                risks.append(f"Gaining currently injured/inactive player: {p.full_name} ({p.injury_status or 'Inactive'})")
            elif p.is_questionable:
                risks.append(f"Gaining questionable status player: {p.full_name}")

        if len(give_players) < len(receive_players):
            roster_impacts.append(f"Roster constraint: will require dropping {len(receive_players) - len(give_players)} player(s)")
        elif len(give_players) > len(receive_players):
            roster_impacts.append(f"Roster space: opens up {len(give_players) - len(receive_players)} bench spot(s)")

        # Auto-detect partner roster if not explicitly provided
        if partner_roster is None and all_rosters:
            partner_counts: Dict[int, int] = {}
            for p in receive_players:
                for r in all_rosters:
                    if r.roster_id != roster.roster_id and p.player_id in r.players:
                        partner_counts[r.roster_id] = partner_counts.get(r.roster_id, 0) + 1
            if partner_counts:
                best_partner_id = max(partner_counts, key=partner_counts.get)
                partner_roster = next((r for r in all_rosters if r.roster_id == best_partner_id), None)

        # Team A (My Team) contextual impact
        give_pids = {p.player_id for p in give_players}
        rec_pids = {p.player_id for p in receive_players}

        my_owner = owner_map.get(roster.roster_id) if owner_map else None
        my_team_name = getattr(my_owner, "team_name", None) or f"Team {roster.roster_id}"
        my_owner_name = getattr(my_owner, "display_name", None) or f"Owner {roster.roster_id}"

        rec_a_before = None
        rec_a_after = None
        if league:
            rec_a_before = self.lineup_optimizer.optimize(roster, league, all_players)
            starters_a_before = rec_a_before.optimal_projected_total

            new_players_a = [pid for pid in roster.players if pid not in give_pids] + [p.player_id for p in receive_players]
            new_starters_a = [pid if pid not in give_pids else "" for pid in roster.starters]
            sim_roster_a = Roster(
                roster_id=roster.roster_id,
                owner_id=roster.owner_id,
                league_id=roster.league_id,
                starters=new_starters_a,
                players=new_players_a,
                settings=roster.settings,
            )
            rec_a_after = self.lineup_optimizer.optimize(sim_roster_a, league, all_players)
            starters_a_after = rec_a_after.optimal_projected_total
            starter_diff_a = round(starters_a_after - starters_a_before, 1)
        else:
            starters_a_before = give_proj
            starters_a_after = rec_proj
            starter_diff_a = round(raw_diff, 1)

        impacts_a = []
        surpluses_a = []
        deficits_a = []

        # Analyze Team A roster depth and surplus
        for p in give_players:
            rem_pos = [pid for pid in roster.players if pid not in give_pids and all_players.get(pid) and all_players[pid].position == p.position]
            if p.position in ("RB", "WR") and len(rem_pos) <= 2:
                deficits_a.append(f"Atenção em {p.position}: seu elenco fica com apenas {len(rem_pos)} {p.position}(s) ativos.")
            elif p.position == "QB" and len(rem_pos) >= 1:
                surpluses_a.append("QB excedente negociado sem comprometer a titularidade.")

        if rec_a_after:
            opt_starters_a = {pid for _, pid in rec_a_after.optimal_starters}
            for p in receive_players:
                if p.player_id in opt_starters_a:
                    impacts_a.append(f"Upgrade Titular: {p.full_name} ({p.position}) assume vaga imediata no time titular.")
                else:
                    impacts_a.append(f"Profundidade de Banco: {p.full_name} ({p.position}) entra como reserva.")
        else:
            for p in receive_players:
                impacts_a.append(f"Adição ao elenco: {p.full_name} ({p.position} - {self.projections.get_projection(p, ppr_bonus=ppr_bonus).projected_points:.1f} pts).")

        my_team_impact = TeamTradeImpact(
            roster_id=roster.roster_id,
            team_name=my_team_name,
            owner_name=my_owner_name,
            give_players=give_players,
            receive_players=receive_players,
            starter_projected_before=round(starters_a_before, 1),
            starter_projected_after=round(starters_a_after, 1),
            starter_projected_diff=starter_diff_a,
            total_projected_before=round(give_proj, 1),
            total_projected_after=round(rec_proj, 1),
            total_projected_diff=round(raw_diff, 1),
            positional_impacts=impacts_a,
            surpluses=surpluses_a,
            deficits=deficits_a,
            verdict="Favorável" if starter_diff_a > 0 else ("Equilibrada" if starter_diff_a == 0 else "Desfavorável"),
            summary=f"Impacto na escalação titular: {starter_diff_a:+.1f} pts por semana.",
        )

        # Team B (Partner Team) contextual impact
        partner_team_impact = None
        starter_diff_b = 0.0
        if partner_roster:
            partner_owner = owner_map.get(partner_roster.roster_id) if owner_map else None
            partner_team_name = getattr(partner_owner, "team_name", None) or f"Team {partner_roster.roster_id}"
            partner_owner_name = getattr(partner_owner, "display_name", None) or f"Owner {partner_roster.roster_id}"

            give_pids_b = {p.player_id for p in receive_players}
            if league:
                rec_b_before = self.lineup_optimizer.optimize(partner_roster, league, all_players)
                starters_b_before = rec_b_before.optimal_projected_total

                new_players_b = [pid for pid in partner_roster.players if pid not in give_pids_b] + [p.player_id for p in give_players]
                new_starters_b = [pid if pid not in give_pids_b else "" for pid in partner_roster.starters]
                sim_roster_b = Roster(
                    roster_id=partner_roster.roster_id,
                    owner_id=partner_roster.owner_id,
                    league_id=partner_roster.league_id,
                    starters=new_starters_b,
                    players=new_players_b,
                    settings=partner_roster.settings,
                )
                rec_b_after = self.lineup_optimizer.optimize(sim_roster_b, league, all_players)
                starters_b_after = rec_b_after.optimal_projected_total
                starter_diff_b = round(starters_b_after - starters_b_before, 1)
            else:
                starters_b_before = rec_proj
                starters_b_after = give_proj
                starter_diff_b = round(-raw_diff, 1)
                rec_b_after = None

            impacts_b = []
            surpluses_b = []
            deficits_b = []

            if rec_b_after:
                opt_starters_b = {pid for _, pid in rec_b_after.optimal_starters}
                for p in give_players:
                    if p.player_id in opt_starters_b:
                        impacts_b.append(f"Upgrade para o Parceiro: {p.full_name} assume vaga no time titular dele.")
                    else:
                        impacts_b.append(f"Reserva para o Parceiro: {p.full_name} entra como banco.")

            for p in receive_players:
                rem_pos_b = [pid for pid in partner_roster.players if pid not in give_pids_b and all_players.get(pid) and all_players[pid].position == p.position]
                if p.position in ("RB", "WR") and len(rem_pos_b) <= 2:
                    deficits_b.append(f"O parceiro fica com apenas {len(rem_pos_b)} {p.position}(s) restantes no elenco.")

            partner_verdict = "Favorável" if starter_diff_b > 0 else ("Equilibrada" if starter_diff_b == 0 else "Desfavorável")
            partner_summary = (
                f"O parceiro ganha {starter_diff_b:+.1f} pts na escalação titular." if starter_diff_b >= 0
                else f"O parceiro perde {abs(starter_diff_b):.1f} pts na escalação titular."
            )

            partner_team_impact = TeamTradeImpact(
                roster_id=partner_roster.roster_id,
                team_name=partner_team_name,
                owner_name=partner_owner_name,
                give_players=receive_players,
                receive_players=give_players,
                starter_projected_before=round(starters_b_before, 1),
                starter_projected_after=round(starters_b_after, 1),
                starter_projected_diff=starter_diff_b,
                total_projected_before=round(rec_proj, 1),
                total_projected_after=round(give_proj, 1),
                total_projected_diff=round(-raw_diff, 1),
                positional_impacts=impacts_b,
                surpluses=surpluses_b,
                deficits=deficits_b,
                verdict=partner_verdict,
                summary=partner_summary,
            )

        # Consolidate Verdict & Feasibility
        negotiation_tips = []
        if partner_team_impact is not None and league is not None:
            if any(p.is_injured or p.is_inactive for p in receive_players):
                verdict = "High Risk"
                feasibility = "Risco Elevado"
                win_win_score = 40.0
                summary = "A proposta envolve ativos lesionados ou inativos, trazendo incerteza à pontuação real."
                negotiation_tips.append("Verifique o relatório médico oficial antes de avançar com o aceite da proposta.")
            elif starter_diff_a >= 0.5 and starter_diff_b >= 0.5:
                verdict = "Win-Win (Favorável aos dois)"
                feasibility = "Alta (Win-Win)"
                win_win_score = round(min(98.0, 85.0 + (starter_diff_a + starter_diff_b) * 2.0), 1)
                summary = f"Proposta Win-Win perfeita: seu time ganha {starter_diff_a:+.1f} pts/sem nos titulares e o parceiro ganha {starter_diff_b:+.1f} pts/sem. Ambos saem fortalecidos."
                negotiation_tips.append("Envie a proposta imediatamente no Sleeper destacando o ganho mútuo de pontuação.")
            elif starter_diff_a >= 1.5 and starter_diff_b < -1.0:
                verdict = "Favorável ao seu Time (Risco de Rejeição)"
                feasibility = "Baixa (Parceiro Perde Pontos)"
                win_win_score = round(max(20.0, 50.0 + starter_diff_b * 10), 1)
                summary = f"Excelente ganho titular para você ({starter_diff_a:+.1f} pts/sem), mas o parceiro perde {abs(starter_diff_b):.1f} pts na formação ideal. Ele tem alta chance de rejeitar."
                negotiation_tips.append("Inclua um reserva de bom piso ou escolha de draft para compensar a perda do parceiro e viabilizar o acordo.")
            elif starter_diff_a < -1.5:
                verdict = "Desfavorável ao seu Time"
                feasibility = "Desaconselhada"
                win_win_score = 25.0
                summary = f"Seu time perde {abs(starter_diff_a):.1f} pts titulares por semana. Proposta desvantajosa para sua franquia."
                negotiation_tips.append("Peça um titular de maior impacto ou recuse a proposta atual.")
            else:
                verdict = "Equilibrada / Troca Tática"
                feasibility = "Moderada (Negociável)"
                win_win_score = 65.0
                summary = f"Troca equilibrada em pontuação ({starter_diff_a:+.1f} pts vs {starter_diff_b:+.1f} pts). O valor reside na cobertura de folgas (Bye) e flexibilidade tática."
                negotiation_tips.append("Negociação equilibrada. Alinhe com o outro GM os planos de elenco para fechar o acordo.")
        else:
            # Fallback legacy 1-team mode (for direct unit tests without league/partner)
            if any(p.is_injured for p in receive_players):
                verdict = "High Risk"
                feasibility = "High Risk"
                win_win_score = 35.0
                summary = "Trade carries injury uncertainty on acquired assets."
            elif raw_diff >= 3.0:
                verdict = "Favorable"
                feasibility = "Favorable"
                win_win_score = 80.0
                summary = f"Positive projection gain (+{raw_diff:.1f} pts/wk). Strengthens roster production."
            elif -1.5 <= raw_diff < 3.0:
                verdict = "Fair / Balanced"
                feasibility = "Fair"
                win_win_score = 60.0
                summary = f"Even value trade ({raw_diff:+.1f} pts/wk). Viable if addressing positional needs."
            else:
                verdict = "Unfavorable"
                feasibility = "Unfavorable"
                win_win_score = 30.0
                summary = f"Net negative projected value ({raw_diff:.1f} pts/wk). Only pursue if desperate for positional replacement."

        return TradeEvaluation(
            give_players=give_players,
            receive_players=receive_players,
            give_total_projected=round(give_proj, 2),
            receive_total_projected=round(rec_proj, 2),
            net_projected_diff=round(raw_diff, 2),
            verdict=verdict,
            roster_impacts=roster_impacts,
            risks=risks,
            summary=summary,
            my_team=my_team_impact,
            partner_team=partner_team_impact,
            win_win_score=win_win_score,
            feasibility=feasibility,
            negotiation_tips=negotiation_tips,
        )
