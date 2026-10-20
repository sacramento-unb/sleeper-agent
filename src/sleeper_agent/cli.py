"""Command-line interface for Sleeper Agent."""

from __future__ import annotations
import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

from sleeper.client import SleeperClient
from sleeper.models import League, Matchup, Player, Roster, User
from sleeper.rosters import find_user_roster, build_roster_owner_map, get_faab_remaining
from sleeper.leagues import get_starting_slots, get_ppr_format
from sleeper.matchups import find_opponent_matchup
from fantasy.lineup import LineupOptimizer
from fantasy.waivers import WaiverOptimizer
from fantasy.trades import TradeAnalyzer
from fantasy.projections import ProjectionEngine
from agent.state import AgentState
from agent.planner import WeeklyGMPlanner
from browser.sleeper_ui import SleeperBrowserDriver
from browser.verification import ActionVerifier


def get_configured_client() -> SleeperClient:
    return SleeperClient()


def resolve_context(
    client: SleeperClient,
    username: Optional[str] = None,
    league_id: Optional[str] = None,
    season: Optional[str] = None,
) -> Tuple[User, League, Roster, List[Roster], List[User], Dict[str, Player]]:
    """Resolves user, target league, rosters, and player database."""
    # 1. User
    user_input = username or os.getenv("SLEEPER_USERNAME") or os.getenv("SLEEPER_USER_ID")
    if not user_input:
        print("[!] No Sleeper username or user ID provided.")
        print("    Specify --user <username> or set SLEEPER_USERNAME in .env")
        sys.exit(1)

    user = client.get_user(user_input)
    if not user:
        print(f"[!] Could not find Sleeper user '{user_input}'. Please check username.")
        sys.exit(1)

    # 2. Season
    if not season:
        season = os.getenv("SLEEPER_SEASON")
        if not season:
            nfl_state = client.get_nfl_state()
            season = nfl_state.season

    # 3. League
    target_league_id = league_id or os.getenv("SLEEPER_LEAGUE_ID")
    target_league: Optional[League] = None

    if target_league_id:
        target_league = client.get_league(target_league_id)
        if not target_league:
            print(f"[!] Could not find league with ID '{target_league_id}'.")
            sys.exit(1)
    else:
        # Auto-discover leagues for user
        leagues = client.get_user_leagues(user.user_id, season)
        if not leagues:
            # Try previous season if current season has no leagues yet
            prev_season = str(int(season) - 1)
            leagues = client.get_user_leagues(user.user_id, prev_season)
            if leagues:
                season = prev_season

        if not leagues:
            print(f"[!] No leagues found for user '{user.display_name}' in season {season}.")
            print("    Please specify --league <league_id> directly.")
            sys.exit(1)

        # Select first league
        target_league = leagues[0]

    # 4. Rosters & Users
    rosters = client.get_rosters(target_league.league_id)
    league_users = client.get_league_users(target_league.league_id)

    # 5. User Roster
    user_roster = find_user_roster(rosters, user.user_id)
    if not user_roster:
        print(f"[!] User '{user.display_name}' does not own an active roster in league '{target_league.name}'.")
        sys.exit(1)

    # 6. Players
    all_players = client.get_all_players()

    return user, target_league, user_roster, rosters, league_users, all_players


def cmd_status(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    owner_map = build_roster_owner_map(all_rosters, users)
    user_meta = owner_map.get(roster.roster_id, user)
    team_name = user_meta.team_name

    nfl_state = client.get_nfl_state()
    week = nfl_state.week
    matchups = client.get_matchups(league.league_id, week)
    opp_m = find_opponent_matchup(matchups, roster.roster_id)
    opp_name = "None (Bye / Off-season)"
    if opp_m and opp_m.roster_id in owner_map:
        opp_name = owner_map[opp_m.roster_id].team_name

    faab_rem = get_faab_remaining(roster, league)
    slots = get_starting_slots(league)
    projections = ProjectionEngine()

    print("=" * 60)
    print(f" LEAGUE: {league.name} ({league.season})")
    print(f" TEAM:   {team_name} (Owner: {user.display_name})")
    print(f" RECORD: {roster.wins}-{roster.losses}" + (f"-{roster.ties}" if roster.ties else ""))
    print(f" POINTS FOR: {roster.points_for:.1f} pts")
    print(f" FAAB REMAINING: ${faab_rem} / ${league.faab_budget}")
    print(f" NEXT OPPONENT (Week {week}): {opp_name}")
    print("=" * 60)

    print("\n[STARTING LINEUP]")
    total_proj = 0.0
    for i, slot in enumerate(slots):
        pid = roster.starters[i] if i < len(roster.starters) else ""
        if pid and pid in all_players:
            p = all_players[pid]
            proj = projections.get_projection(p)
            total_proj += proj.projected_points
            status_flag = f" [{p.injury_status}]" if p.injury_status else ""
            print(f"  {slot:<10} {p.full_name:<24} ({p.team or 'FA'} - {p.position}) {proj.projected_points:>5.1f} pts{status_flag}")
        else:
            print(f"  {slot:<10} {'[EMPTY]':<24}       0.0 pts")

    print(f"  {'TOTAL PROJECTED:':<35} {total_proj:>5.1f} pts")

    print("\n[BENCH]")
    bench_pids = roster.bench_players
    for pid in bench_pids:
        if pid in all_players:
            p = all_players[pid]
            proj = projections.get_projection(p)
            status_flag = f" [{p.injury_status}]" if p.injury_status else ""
            print(f"  {'BN':<10} {p.full_name:<24} ({p.team or 'FA'} - {p.position}) {proj.projected_points:>5.1f} pts{status_flag}")

    if roster.reserve:
        print("\n[IR / RESERVE]")
        for pid in roster.reserve:
            if pid in all_players:
                p = all_players[pid]
                print(f"  {'IR':<10} {p.full_name:<24} ({p.team or 'FA'} - {p.position}) [{p.injury_status or 'IR'}]")

    print("=" * 60)


def cmd_lineup(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    optimizer = LineupOptimizer()
    rec = optimizer.optimize(roster, league, all_players)

    print("=" * 60)
    print(f" LINEUP OPTIMIZER: {league.name}")
    print(f" Current Lineup Projected: {rec.current_projected_total:.1f} pts")
    print(f" Optimal Lineup Projected: {rec.optimal_projected_total:.1f} pts")
    diff = rec.optimal_projected_total - rec.current_projected_total
    print(f" Potential Improvement:   {diff:+.1f} pts")
    print("=" * 60)

    if rec.warnings:
        print("\n[!] INJURY & AVAILABILITY ALERTS:")
        for w in rec.warnings:
            print(f"  • {w}")

    if not rec.moves:
        print("\n[✓] Your starting lineup is already optimal! No changes required.")
    else:
        print(f"\n[+] RECOMMENDED LINEUP SWAPS ({len(rec.moves)}):")
        state = AgentState.load()
        for i, m in enumerate(rec.moves, 1):
            print(f"  {i}. START: {m.start_player_name} [{m.slot}]")
            print(f"     BENCH: {m.bench_player_name}")
            print(f"     Reason: {m.reason} ({m.projected_diff:+.1f} pts)")

            if args.optimize:
                action = LineupChangeAction(
                    bench_player_id=m.bench_player_id,
                    start_player_id=m.start_player_id,
                    slot=m.slot,
                    reason=m.reason,
                    bench_player_name=m.bench_player_name,
                    start_player_name=m.start_player_name,
                )
                state.add_pending_action(action)
                print(f"     -> Created pending action: {action.action_id} (Approval Required)")

        if args.optimize:
            state.save()
            print("\nSaved pending actions to agent state.")
            print("Run 'python -m sleeper_agent exec <action_id>' to approve and execute.")

    print("=" * 60)


def cmd_waivers(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    optimizer = WaiverOptimizer()
    rec = optimizer.analyze(roster, league, all_players, all_rosters, top_n=args.top)

    print("=" * 60)
    print(f" WAIVER WIRE & FAAB OPTIMIZER: {league.name}")
    print(f" Remaining FAAB: ${rec.faab_remaining} / ${rec.faab_budget}")
    print("=" * 60)

    print(f"\n[TOP {len(rec.targets)} RECOMMENDED WAIVER TARGETS]")
    for i, t in enumerate(rec.targets, 1):
        print(f"  Priority {i}: {t.player_name} ({t.team or 'FA'} - {t.position})")
        print(f"    Projected Points: {t.projected_points:.1f} pts")
        print(f"    Recommended Bid:  ${t.recommended_faab}")
        print(f"    Reason:           {t.reason}")

    if rec.fallbacks:
        print("\n[FALLBACK WAIVER TARGETS]")
        for fb in rec.fallbacks:
            print(f"  • {fb.player_name} ({fb.position}) - ${fb.recommended_faab} ({fb.projected_points:.1f} pts)")

    print(f"\n[TOP {len(rec.drop_candidates)} DROP CANDIDATES]")
    for d in rec.drop_candidates:
        print(f"  • {d.player_name} ({d.position}) [{d.projected_points:.1f} pts]: {d.reason}")

    print("=" * 60)


def cmd_matchup(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    nfl_state = client.get_nfl_state()
    week = args.week or nfl_state.week
    matchups = client.get_matchups(league.league_id, week)
    owner_map = build_roster_owner_map(all_rosters, users)

    my_m = next((m for m in matchups if m.roster_id == roster.roster_id), None)
    opp_m = find_opponent_matchup(matchups, roster.roster_id)

    projections = ProjectionEngine()
    my_proj = sum(
        projections.get_projection(all_players[pid]).projected_points
        for pid in (my_m.starters if my_m else roster.starters)
        if pid in all_players
    )

    opp_proj = 0.0
    opp_name = "Opponent"
    if opp_m:
        opp_proj = sum(
            projections.get_projection(all_players[pid]).projected_points
            for pid in opp_m.starters
            if pid in all_players
        )
        if opp_m.roster_id in owner_map:
            opp_name = owner_map[opp_m.roster_id].team_name

    diff = my_proj - opp_proj
    spread_str = f"Favored by {diff:.1f} pts" if diff >= 0 else f"Underdog by {abs(diff):.1f} pts"

    print("=" * 60)
    print(f" WEEK {week} MATCHUP PREVIEW")
    print(f" {owner_map[roster.roster_id].team_name} vs {opp_name}")
    print(f" Projection: {my_proj:.1f} pts vs {opp_proj:.1f} pts ({spread_str})")
    print("=" * 60)

    if my_m:
        print(f" Current In-Game Score: {my_m.points:.2f} pts vs {opp_m.points if opp_m else 0.0:.2f} pts")

    print("=" * 60)


def cmd_trade(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    give_ids = [s.strip() for s in (args.give or "").split(",") if s.strip()]
    receive_ids = [s.strip() for s in (args.receive or "").split(",") if s.strip()]

    if not give_ids or not receive_ids:
        print("[!] Please provide both --give and --receive player IDs or names.")
        print("    Example: python -m sleeper_agent trade --give 4034 --receive 6794")
        return

    # Helper to resolve names to player_ids if user passed names
    def resolve_pids(items: List[str]) -> List[str]:
        resolved = []
        for it in items:
            if it in all_players:
                resolved.append(it)
            else:
                matches = [p.player_id for p in all_players.values() if it.lower() in p.full_name.lower()]
                if matches:
                    resolved.append(matches[0])
                else:
                    resolved.append(it)
        return resolved

    g_pids = resolve_pids(give_ids)
    r_pids = resolve_pids(receive_ids)

    analyzer = TradeAnalyzer()
    res = analyzer.evaluate_trade(roster, g_pids, r_pids, all_players, league)

    print("=" * 60)
    print(" TRADE EVALUATION & IMPACT REPORT")
    print(f" Verdict: {res.verdict.upper()}")
    print("=" * 60)
    print(f" Giving ({res.give_total_projected:.1f} pts):")
    for p in res.give_players:
        print(f"  • {p.full_name} ({p.team or 'FA'} - {p.position})")

    print(f"\n Receiving ({res.receive_total_projected:.1f} pts):")
    for p in res.receive_players:
        print(f"  • {p.full_name} ({p.team or 'FA'} - {p.position})")

    print(f"\n Net Projected Difference: {res.net_projected_diff:+.1f} pts / week")
    print(f" Summary: {res.summary}")

    if res.roster_impacts:
        print("\n Roster Depth Impact:")
        for imp in res.roster_impacts:
            print(f"  • {imp}")

    if res.risks:
        print("\n Risk Factors:")
        for rk in res.risks:
            print(f"  [!] {rk}")

    print("=" * 60)


def cmd_workflow(args: argparse.Namespace) -> None:
    client = get_configured_client()
    user, league, roster, all_rosters, users, all_players = resolve_context(
        client, args.user, args.league, args.season
    )

    nfl_state = client.get_nfl_state()
    matchups = client.get_matchups(league.league_id, nfl_state.week)

    planner = WeeklyGMPlanner(client, league, user.user_id)
    day = args.day.lower()

    if day in ("tuesday", "tue"):
        res = planner.run_tuesday_waiver_report(all_players, all_rosters)
    elif day in ("thursday", "thu"):
        res = planner.run_thursday_tnf_check(all_players, all_rosters)
    elif day in ("sunday", "sun"):
        res = planner.run_sunday_lineup_lock(all_players, all_rosters, matchups, users)
    else:
        print(f"[!] Unknown workflow day '{args.day}'. Choose tuesday, thursday, or sunday.")
        return

    print("=" * 60)
    print(f" {res.headline.upper()}")
    print("=" * 60)
    for line in res.summary_lines:
        print(line)

    if res.proposed_actions:
        state = AgentState.load()
        print(f"\nProposed Structured Actions ({len(res.proposed_actions)}):")
        for act in res.proposed_actions:
            state.add_pending_action(act)
            print(f"  • [{act.action_id}] {act.action_type.value}: {act.reason} (Approval Required)")
        state.save()
        print("\nSaved pending actions to state.")
    print("=" * 60)


def cmd_sync(args: argparse.Namespace) -> None:
    print("[*] Synchronizing Sleeper player database (force refresh)...")
    client = get_configured_client()
    players = client.get_all_players(force_refresh=True)
    print(f"[✓] Successfully downloaded and cached {len(players)} NFL players.")


def cmd_exec(args: argparse.Namespace) -> None:
    state = AgentState.load()
    action = state.get_action_by_id(args.action_id)
    if not action:
        print(f"[!] Action ID '{args.action_id}' not found in pending actions.")
        return

    print(f"Action: {action.action_type.value} [{action.action_id}]")
    print(f"Reason: {action.reason}")
    print(f"Parameters: {action.params}")
    print(f"Requires Approval: {action.requires_approval}")

    if not args.yes:
        confirm = input("\nApprove and execute this action on Sleeper? [y/N]: ").strip().lower()
        if confirm != "y":
            print("[x] Action cancelled by user.")
            return

    action.approved = True
    print("\n[*] Approval granted. Initializing browser automation driver...")
    driver = SleeperBrowserDriver()
    league_id = os.getenv("SLEEPER_LEAGUE_ID", "default_league")

    res = driver.execute_action(action, league_id)
    print(f"[✓] Execution status: {res.get('message')}")
    if res.get("simulated"):
        print("    Mode: DRY-RUN SIMULATION (Safe / No live write performed)")
        for step in res.get("steps", []):
            print(f"      -> {step}")

    state.save()
    print("[✓] State updated successfully.")


def cmd_admin_password(args: argparse.Namespace) -> None:
    from storage.db import Database, ADMIN_EMAIL
    db = Database()
    new_pw = args.password
    db.set_admin_password(new_pw)
    print(f"[✓] Senha do administrador ({ADMIN_EMAIL}) definida com sucesso para: {new_pw}")


def cmd_reset_admin(args: argparse.Namespace) -> None:
    from storage.db import Database, ADMIN_EMAIL
    db = Database()
    db.reset_admin_to_pending()
    print(f"[✓] Administrador ({ADMIN_EMAIL}) resetado com sucesso para 'Primeiro Acesso'.")
    print("    Acesse o sistema pela web na aba 'Primeiro Acesso' para cadastrar sua nova senha.")


def cmd_web(args: argparse.Namespace) -> None:
    from sleeper_agent.web import run_server
    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = int(args.port or os.getenv("PORT", 8000))
    run_server(host=host, port=port)



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sleeper Fantasy Football AI Agent & General Manager CLI"
    )
    parser.add_argument("--user", help="Sleeper username or user ID")
    parser.add_argument("--league", help="Sleeper league ID")
    parser.add_argument("--season", help="NFL season year (e.g. 2024)")

    subparsers = parser.add_subparsers(dest="command", help="Agent command to execute")

    # status
    subparsers.add_parser("status", help="Display full team snapshot, starters, bench, record, and next opponent")

    # lineup
    p_lineup = subparsers.add_parser("lineup", help="Analyze starting lineup and start/sit recommendations")
    p_lineup.add_argument("--optimize", action="store_true", help="Queue proposed lineup swaps as pending actions")

    # waivers
    p_waivers = subparsers.add_parser("waivers", help="Analyze waiver targets, FAAB bids, and drop candidates")
    p_waivers.add_argument("--top", type=int, default=5, help="Number of waiver targets to return")

    # matchup
    p_matchup = subparsers.add_parser("matchup", help="Inspect head-to-head weekly matchup")
    p_matchup.add_argument("--week", type=int, help="Target week number")

    # trade
    p_trade = subparsers.add_parser("trade", help="Evaluate fantasy trade proposal")
    p_trade.add_argument("--give", required=True, help="Comma-separated player IDs or names to give away")
    p_trade.add_argument("--receive", required=True, help="Comma-separated player IDs or names to receive")

    # workflow
    p_workflow = subparsers.add_parser("workflow", help="Run scheduled weekly GM workflow")
    p_workflow.add_argument("day", choices=["tuesday", "thursday", "sunday"], help="Workflow day")

    # sync
    subparsers.add_parser("sync", help="Force sync and refresh player cache from Sleeper")

    # exec
    p_exec = subparsers.add_parser("exec", help="Approve and execute a pending structured action")
    p_exec.add_argument("action_id", help="Action ID to execute")
    p_exec.add_argument("-y", "--yes", action="store_true", help="Auto-confirm approval")

    # web
    p_web = subparsers.add_parser("web", help="Launch Persone-inspired graphical web dashboard")
    p_web.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    p_web.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")

    # admin-password
    p_admin_pw = subparsers.add_parser("admin-password", help="Set the administrator password directly")
    p_admin_pw.add_argument("password", help="New administrator password (min 6 characters)")

    # reset-admin
    subparsers.add_parser("reset-admin", help="Reset administrator to pending_password state for first-access activation")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "status": cmd_status,
        "lineup": cmd_lineup,
        "waivers": cmd_waivers,
        "matchup": cmd_matchup,
        "trade": cmd_trade,
        "workflow": cmd_workflow,
        "sync": cmd_sync,
        "exec": cmd_exec,
        "web": cmd_web,
        "admin-password": cmd_admin_password,
        "reset-admin": cmd_reset_admin,
    }


    try:
        commands[args.command](args)
        return 0
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 1
