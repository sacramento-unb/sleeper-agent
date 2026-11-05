"""Web server and REST API for Sleeper Agent graphical interface."""

from __future__ import annotations
import json
import logging
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from sleeper.client import SleeperClient
from sleeper.models import League, Matchup, Player, Roster, User
from sleeper.rosters import find_user_roster, build_roster_owner_map, get_faab_remaining
from sleeper.leagues import get_starting_slots, get_ppr_format, get_league_summary
from sleeper.matchups import find_opponent_matchup
from fantasy.lineup import LineupOptimizer
from fantasy.waivers import WaiverOptimizer
from fantasy.trades import TradeAnalyzer
from fantasy.projections import ProjectionEngine
from agent.actions import Action, LineupChangeAction, WaiverClaimAction
from agent.state import AgentState
from agent.planner import WeeklyGMPlanner
from agent.llm import LLMReasoningEngine
from browser.sleeper_ui import SleeperBrowserDriver
from storage.db import get_db, Database, ADMIN_EMAIL

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SleeperAgentRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler providing REST API and serving frontend UI."""

    client: SleeperClient = SleeperClient()
    optimizer: LineupOptimizer = LineupOptimizer()
    waiver_optimizer: WaiverOptimizer = WaiverOptimizer()
    trade_analyzer: TradeAnalyzer = TradeAnalyzer()
    llm_reasoner: LLMReasoningEngine = LLMReasoningEngine()
    browser_driver: SleeperBrowserDriver = SleeperBrowserDriver()
    db: Database = get_db()
    all_players_cache: Optional[Dict[str, Player]] = None

    @classmethod
    def get_players(cls) -> Dict[str, Player]:
        if cls.all_players_cache is None:
            cls.all_players_cache = cls.client.get_all_players()
        return cls.all_players_cache

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _send_json(self, data: Any, status: int = 200, set_cookie: Optional[str] = None) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message, "status": status}, status=status)

    def _get_authenticated_user(self) -> Optional[Dict[str, Any]]:
        """Extract authenticated user session from Authorization header or Cookie."""
        token = ""
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            cookie_header = self.headers.get("Cookie", "")
            if cookie_header:
                for cookie in cookie_header.split(";"):
                    cookie = cookie.strip()
                    if cookie.startswith("session_token="):
                        token = cookie.split("=", 1)[1].strip()
                        break
        if not token:
            return None
        return self.db.get_session(token)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Serve Frontend
        if path in ("/", "/index.html"):
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                self._send_error("Static index.html not found", 404)
                return

        # API: Health
        if path == "/api/health":
            self._send_json({"status": "healthy", "service": "sleeper-agent-api"})
            return

        # API: Project Configuration
        if path == "/api/config":
            self._send_json({
                "configured_league_id": os.getenv("SLEEPER_LEAGUE_ID", ""),
                "configured_username": os.getenv("SLEEPER_USERNAME", ""),
                "configured_season": os.getenv("SLEEPER_SEASON", "2024"),
            })
            return

        # API: Auth - Current User Status
        if path == "/api/auth/me":
            user = self._get_authenticated_user()
            if not user:
                self._send_json({"authenticated": False})
            else:
                self._send_json({"authenticated": True, "user": user})
            return

        # API: Admin - List Users (strictly restricted to the configured admin account)
        if path == "/api/admin/users":
            user = self._get_authenticated_user()
            if not user:
                self._send_error("Não autorizado. Por favor, faça login.", 401)
                return
            if not user.get("is_admin") and user.get("email", "").lower() != ADMIN_EMAIL.lower():
                self._send_error(f"Acesso restrito ao administrador ({ADMIN_EMAIL}).", 403)
                return
            self._send_json({"users": self.db.list_users()})
            return

        # API: League Seasons / Years for configured Docker League
        if path == "/api/league/seasons":
            user = self._get_authenticated_user()
            if not user:
                self._send_error("Acesso não autorizado. Por favor, faça login.", 401)
                return
            self._handle_league_seasons()
            return

        # API: Get League details and Teams (Protected)
        if path.startswith("/api/league/"):
            user = self._get_authenticated_user()
            if not user:
                self._send_error("Acesso não autorizado. Por favor, faça login.", 401)
                return

            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "league":
                league_id = parts[2]

                # Sub-route: Team Dashboard
                # /api/league/<league_id>/team/<roster_id>/dashboard
                if len(parts) == 6 and parts[3] == "team" and parts[5] == "dashboard":
                    try:
                        roster_id = int(parts[4])
                    except ValueError:
                        self._send_error("Invalid roster_id")
                        return
                    self._handle_team_dashboard(league_id, roster_id, query)
                    return

                # Sub-route: Team GM Insights (Gemini Flash)
                # /api/league/<league_id>/team/<roster_id>/gm-insights
                if len(parts) == 6 and parts[3] == "team" and parts[5] == "gm-insights":
                    try:
                        roster_id = int(parts[4])
                    except ValueError:
                        self._send_error("Invalid roster_id")
                        return
                    self._handle_team_gm_insights(league_id, roster_id, query)
                    return

                # Sub-route: Team Workflow
                # /api/league/<league_id>/team/<roster_id>/workflow
                if len(parts) == 6 and parts[3] == "team" and parts[5] == "workflow":
                    try:
                        roster_id = int(parts[4])
                    except ValueError:
                        self._send_error("Invalid roster_id")
                        return
                    day = query.get("day", ["sunday"])[0].lower()
                    self._handle_team_workflow(league_id, roster_id, day)
                    return

                # Main League details & Teams listing
                if len(parts) == 3:
                    force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                    self._handle_league_overview(league_id, force_refresh=force_refresh)
                    return

        self._send_error("Endpoint not found", 404)

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # Read JSON body
        content_len = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_len > 0:
            try:
                body = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                body = {}

        # API: Auth - Login
        if path == "/api/auth/login":
            email = (body.get("email") or "").strip().lower()
            password = body.get("password") or ""
            try:
                auth_data = self.db.authenticate_user(email, password)
                auth_data["token"] = auth_data["session_token"]
                cookie = f"session_token={auth_data['session_token']}; Path=/; HttpOnly; SameSite=Lax; Max-Age=259200"
                self._send_json(auth_data, set_cookie=cookie)
            except ValueError as e:
                self._send_error(str(e), 400)
            except Exception as e:
                logger.exception("Error during login")
                self._send_error("Erro interno ao autenticar.", 500)
            return

        # API: Auth - First Access (create initial password)
        if path == "/api/auth/first-access":
            email = (body.get("email") or "").strip().lower()
            password = body.get("password") or ""
            try:
                user_data = self.db.set_first_access_password(email, password)
                user_data["token"] = user_data["session_token"]
                cookie = f"session_token={user_data['session_token']}; Path=/; HttpOnly; SameSite=Lax; Max-Age=259200"
                self._send_json({
                    "status": "ok",
                    "message": "Senha criada com sucesso! Acesso ativado.",
                    "user": user_data,
                    "session_token": user_data["session_token"],
                    "token": user_data["session_token"],
                }, set_cookie=cookie)
            except ValueError as e:
                self._send_error(str(e), 400)
            except Exception as e:
                logger.exception("Error during first access")
                self._send_error("Erro interno no primeiro acesso.", 500)
            return

        # API: Auth - Logout
        if path == "/api/auth/logout":
            auth_user = self._get_authenticated_user()
            if auth_user:
                self.db.invalidate_session(auth_user["token"])
            clear_cookie = "session_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            self._send_json({"status": "ok"}, set_cookie=clear_cookie)
            return

        # API: Auth - Reset Password (using token)
        if path == "/api/auth/reset-password":
            token = (body.get("token") or "").strip()
            new_password = body.get("new_password") or ""
            if not token:
                self._send_error("Token de redefinição obrigatório.", 400)
                return
            try:
                self.db.reset_password_with_token(token, new_password)
                self._send_json({
                    "status": "ok",
                    "message": "Senha redefinida com sucesso! Você já pode entrar com sua nova senha.",
                })
            except ValueError as e:
                self._send_error(str(e), 400)
            except Exception as e:
                logger.exception("Error during password reset")
                self._send_error("Erro interno ao redefinir senha.", 500)
            return

        # API: Admin - Register new user email (Admin only)
        if path == "/api/admin/users":
            user = self._get_authenticated_user()
            if not user:
                self._send_error("Não autorizado. Faça login.", 401)
                return
            if not user.get("is_admin") and user.get("email", "").lower() != ADMIN_EMAIL.lower():
                self._send_error(f"Acesso restrito ao administrador ({ADMIN_EMAIL}).", 403)
                return
            email = (body.get("email") or "").strip().lower()
            try:
                new_user = self.db.register_user(email)
                self._send_json({
                    "status": "ok",
                    "message": f"Usuário '{email}' cadastrado com sucesso. O usuário deve agora criar sua senha na aba 'Primeiro Acesso'.",
                    "user": new_user,
                })
            except ValueError as e:
                self._send_error(str(e), 400)
            except Exception as e:
                logger.exception("Error registering user")
                self._send_error("Erro interno ao cadastrar usuário.", 500)
            return

        # API: Admin - Reset user password / Generate reset token (Admin only)
        if path == "/api/admin/users/reset":
            user = self._get_authenticated_user()
            if not user:
                self._send_error("Não autorizado. Faça login.", 401)
                return
            if not user.get("is_admin") and user.get("email", "").lower() != ADMIN_EMAIL.lower():
                self._send_error(f"Acesso restrito ao administrador ({ADMIN_EMAIL}).", 403)
                return
            email = (body.get("email") or "").strip().lower()
            direct_pw = (body.get("direct_password") or "").strip()
            try:
                if direct_pw:
                    self.db.admin_reset_user_password(email, direct_pw)
                    self._send_json({
                        "status": "ok",
                        "message": f"Nova senha definida com sucesso para '{email}'.",
                    })
                else:
                    token = self.db.create_password_reset_token(email)
                    self._send_json({
                        "status": "ok",
                        "email": email,
                        "reset_token": token,
                        "message": f"Token gerado para '{email}': {token}. Forneça este token ao usuário para redefinir a senha.",
                    })
            except ValueError as e:
                self._send_error(str(e), 400)
            except Exception as e:
                logger.exception("Error resetting user password")
                self._send_error("Erro interno ao redefinir senha do usuário.", 500)
            return

        # Protected endpoints below require login
        auth_user = self._get_authenticated_user()
        if not auth_user:
            self._send_error("Acesso não autorizado. Por favor, realize o login.", 401)
            return

        # API: Execute Pending Action
        # POST /api/actions/<action_id>/execute
        if path.startswith("/api/actions/") and path.endswith("/execute"):
            parts = [p for p in path.split("/") if p]
            if len(parts) == 4:
                action_id = parts[2]
                state = AgentState.load()
                action = state.get_action_by_id(action_id)
                if not action:
                    self._send_error(f"Action '{action_id}' not found in state", 404)
                    return

                action.approved = True
                league_id = body.get("league_id") or os.getenv("SLEEPER_LEAGUE_ID", "default")
                try:
                    res = self.browser_driver.execute_action(action, league_id)
                    state.save()
                    self._send_json(res)
                except Exception as e:
                    self._send_error(f"Execution failed: {e}", 500)
                return

        # API: Trade Analyzer
        # POST /api/league/<league_id>/team/<roster_id>/trade
        if path.startswith("/api/league/") and path.endswith("/trade"):
            parts = [p for p in path.split("/") if p]
            if len(parts) == 6 and parts[3] == "team":
                league_id = parts[2]
                try:
                    roster_id = int(parts[4])
                except ValueError:
                    self._send_error("Invalid roster_id")
                    return

                give_ids = body.get("give", [])
                receive_ids = body.get("receive", [])
                partner_roster_id = body.get("partner_roster_id")
                try:
                    partner_roster_id = int(partner_roster_id) if partner_roster_id is not None and str(partner_roster_id).strip() != "" else None
                except (ValueError, TypeError):
                    partner_roster_id = None

                league = self.client.get_league(league_id)
                rosters = self.client.get_rosters(league_id)
                roster = next((r for r in rosters if r.roster_id == roster_id), None)
                if not roster:
                    self._send_error("Roster not found", 404)
                    return

                partner_roster = next((r for r in rosters if r.roster_id == partner_roster_id), None) if partner_roster_id else None
                users = self.client.get_league_users(league_id)
                owner_map = build_roster_owner_map(rosters, users)

                all_players = self.get_players()
                ppr_bonus = float(league.scoring_settings.get("rec", 0.5)) if league and league.scoring_settings else 0.5

                trade_res = self.trade_analyzer.evaluate_trade(
                    roster=roster,
                    give_player_ids=give_ids,
                    receive_player_ids=receive_ids,
                    all_players=all_players,
                    league=league,
                    partner_roster=partner_roster,
                    all_rosters=rosters,
                    owner_map=owner_map,
                )

                self._send_json({
                    "verdict": trade_res.verdict,
                    "give_total_projected": trade_res.give_total_projected,
                    "receive_total_projected": trade_res.receive_total_projected,
                    "net_projected_diff": trade_res.net_projected_diff,
                    "roster_impacts": trade_res.roster_impacts,
                    "risks": trade_res.risks,
                    "summary": trade_res.summary,
                    "win_win_score": trade_res.win_win_score,
                    "feasibility": trade_res.feasibility,
                    "negotiation_tips": trade_res.negotiation_tips,
                    "give_players": [
                        {
                            "player_id": p.player_id,
                            "name": p.full_name,
                            "position": p.position,
                            "team": p.team or "FA",
                            "projected_points": round(self.trade_analyzer.projections.get_projection(p, ppr_bonus=ppr_bonus).projected_points, 1),
                        }
                        for p in trade_res.give_players
                    ],
                    "receive_players": [
                        {
                            "player_id": p.player_id,
                            "name": p.full_name,
                            "position": p.position,
                            "team": p.team or "FA",
                            "projected_points": round(self.trade_analyzer.projections.get_projection(p, ppr_bonus=ppr_bonus).projected_points, 1),
                        }
                        for p in trade_res.receive_players
                    ],
                    "my_team": trade_res.my_team_dict,
                    "partner_team": trade_res.partner_team_dict,
                })
                return

        self._send_error("Endpoint not found", 404)

    def _handle_league_seasons(self) -> None:
        """Trace available seasons for the Docker-configured league."""
        configured_id = os.getenv("SLEEPER_LEAGUE_ID", "demo-league-id")
        configured_season = os.getenv("SLEEPER_SEASON", "2026")
        cache_key = f"league_seasons:{configured_id}"

        cached = self.db.get_cached_query(cache_key)
        if cached:
            self._send_json(cached)
            return

        seasons_list = []
        curr_id = configured_id
        visited = set()
        league_name = "Minha Liga"

        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            lg = self.client.get_league(curr_id)
            if not lg:
                break
            if not league_name or league_name == "Minha Liga":
                league_name = lg.name
            seasons_list.append({
                "season": lg.season,
                "league_id": lg.league_id,
                "name": lg.name,
                "total_teams": lg.total_rosters,
                "is_current": (lg.league_id == configured_id),
            })
            curr_id = lg.previous_league_id

        seasons_list.sort(key=lambda s: s["season"], reverse=True)
        res = {
            "configured_league_id": configured_id,
            "configured_season": configured_season,
            "league_name": league_name,
            "seasons": seasons_list,
        }
        self.db.set_cached_query(cache_key, res, ttl_seconds=86400)
        self._send_json(res)

    def _handle_league_overview(self, league_id: str, force_refresh: bool = False) -> None:
        cache_key = f"league_overview:{league_id}"
        if not force_refresh:
            cached = self.db.get_cached_query(cache_key)
            if cached:
                cached["cached"] = True
                self._send_json(cached)
                return

        league = self.client.get_league(league_id)
        if not league:
            self._send_error("League not found", 404)
            return

        rosters = self.client.get_rosters(league_id)
        users = self.client.get_league_users(league_id)
        owner_map = build_roster_owner_map(rosters, users)

        is_faab = (league.waiver_type == "faab")
        teams_data = []
        for r in rosters:
            owner = owner_map.get(r.roster_id, User(user_id="", username=f"team_{r.roster_id}", display_name=f"Team {r.roster_id}"))
            faab_rem = get_faab_remaining(r, league) if is_faab else 0
            teams_data.append({
                "roster_id": r.roster_id,
                "owner_id": r.owner_id,
                "team_name": owner.team_name,
                "owner_username": owner.username,
                "owner_display_name": owner.display_name,
                "avatar": owner.avatar,
                "wins": r.wins,
                "losses": r.losses,
                "ties": r.ties,
                "points_for": r.points_for,
                "waiver_position": r.waiver_position,
                "is_faab": is_faab,
                "faab_remaining": faab_rem,
                "starters_count": len(r.starters),
                "players_count": len(r.players),
            })

        teams_data.sort(key=lambda t: (t["wins"], t["points_for"]), reverse=True)

        res = {
            "league": get_league_summary(league),
            "teams": teams_data,
            "cached": False,
        }
        self.db.set_cached_query(cache_key, res, ttl_seconds=1800)
        self._send_json(res)

    def _handle_team_dashboard(self, league_id: str, roster_id: int, query: Optional[Dict[str, List[str]]] = None) -> None:
        force_refresh = False
        selected_week_param = None
        if query:
            force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
            if "week" in query:
                try:
                    selected_week_param = int(query["week"][0])
                except (ValueError, TypeError):
                    selected_week_param = None

        if selected_week_param is not None:
            selected_week = selected_week_param
        else:
            nfl_state = self.client.get_nfl_state()
            selected_week = nfl_state.week

        cache_key = f"team_dashboard:{league_id}:{roster_id}:week{selected_week}"
        if not force_refresh:
            cached = self.db.get_cached_query(cache_key)
            if cached:
                cached["cached"] = True
                self._send_json(cached)
                return

        league = self.client.get_league(league_id)
        if not league:
            self._send_error("League not found", 404)
            return

        rosters = self.client.get_rosters(league_id)
        users = self.client.get_league_users(league_id)
        user_roster = next((r for r in rosters if r.roster_id == roster_id), None)
        if not user_roster:
            self._send_error("Roster not found", 404)
            return

        owner_map = build_roster_owner_map(rosters, users)
        owner = owner_map.get(roster_id, User(user_id="", username=f"team_{roster_id}", display_name=f"Team {roster_id}"))
        all_players = self.get_players()
        
        projections = ProjectionEngine()
        projections.set_player_database(all_players)
        ppr_bonus = float(league.scoring_settings.get("rec", 0.5))
        is_faab = (league.waiver_type == "faab")
        faab_rem = get_faab_remaining(user_roster, league) if is_faab else 0

        # 1. Starters breakdown
        starting_slots = get_starting_slots(league)
        starters_details = []
        for i, slot in enumerate(starting_slots):
            pid = user_roster.starters[i] if i < len(user_roster.starters) else ""
            if pid and pid in all_players:
                p = all_players[pid]
                proj = projections.get_projection(p, ppr_bonus=ppr_bonus)
                starters_details.append({
                    "slot": slot,
                    "player_id": p.player_id,
                    "name": p.full_name,
                    "team": p.team or "FA",
                    "position": p.position,
                    "injury_status": p.injury_status,
                    "is_injured": p.is_injured,
                    "is_questionable": p.is_questionable,
                    "projected_points": proj.projected_points,
                    "floor": proj.floor,
                    "ceiling": proj.ceiling,
                })
            else:
                starters_details.append({
                    "slot": slot,
                    "player_id": "",
                    "name": "[EMPTY]",
                    "team": "",
                    "position": "",
                    "injury_status": None,
                    "is_injured": False,
                    "is_questionable": False,
                    "projected_points": 0.0,
                    "floor": 0.0,
                    "ceiling": 0.0,
                })

        # 2. Bench breakdown
        bench_details = []
        for pid in user_roster.bench_players:
            if pid in all_players:
                p = all_players[pid]
                proj = projections.get_projection(p, ppr_bonus=ppr_bonus)
                bench_details.append({
                    "player_id": p.player_id,
                    "name": p.full_name,
                    "team": p.team or "FA",
                    "position": p.position,
                    "injury_status": p.injury_status,
                    "projected_points": proj.projected_points,
                })

        # 3. Lineup Optimization
        lineup_rec = self.optimizer.optimize(user_roster, league, all_players)

        # 4. Waiver Wire Recommendations
        waiver_rec = self.waiver_optimizer.analyze(user_roster, league, all_players, rosters, top_n=6)

        # 5. Matchup Preview
        nfl_state = self.client.get_nfl_state()
        current_week = nfl_state.week

        matchups = self.client.get_matchups(league_id, selected_week)
        my_m = next((m for m in matchups if m.roster_id == roster_id), None)
        opp_m = find_opponent_matchup(matchups, roster_id)

        opp_name = "Opponent"
        opp_points = opp_m.points if opp_m else 0.0
        opp_projected = 0.0
        if opp_m:
            if opp_m.roster_id in owner_map:
                opp_name = owner_map[opp_m.roster_id].team_name
            opp_projected = sum(
                projections.get_projection(all_players[pid], ppr_bonus=ppr_bonus).projected_points
                for pid in opp_m.starters
                if pid in all_players
            )

        my_actual_points = my_m.points if my_m else 0.0
        is_final = (selected_week < current_week) or (my_actual_points > 0 and opp_points > 0 and selected_week < current_week)

        # Starter-by-starter head-to-head comparison
        starter_comparisons = []
        for i, slot in enumerate(starting_slots):
            my_pid = my_m.starters[i] if (my_m and i < len(my_m.starters)) else (user_roster.starters[i] if i < len(user_roster.starters) else "")
            opp_pid = opp_m.starters[i] if (opp_m and i < len(opp_m.starters)) else ""

            my_p = all_players.get(my_pid)
            opp_p = all_players.get(opp_pid)

            my_proj_val = projections.get_projection(my_p, ppr_bonus=ppr_bonus).projected_points if my_p else 0.0
            opp_proj_val = projections.get_projection(opp_p, ppr_bonus=ppr_bonus).projected_points if opp_p else 0.0

            my_actual_val = my_m.players_points.get(my_pid, 0.0) if my_m else 0.0
            opp_actual_val = opp_m.players_points.get(opp_pid, 0.0) if opp_m else 0.0

            starter_comparisons.append({
                "slot": slot,
                "my_player": my_p.full_name if my_p else "[EMPTY]",
                "my_team": my_p.team if my_p else "",
                "my_projected": my_proj_val,
                "my_actual": my_actual_val,
                "opp_player": opp_p.full_name if opp_p else "[EMPTY]",
                "opp_team": opp_p.team if opp_p else "",
                "opp_projected": opp_proj_val,
                "opp_actual": opp_actual_val,
            })

        matchup_info = {
            "week": selected_week,
            "current_nfl_week": current_week,
            "is_final": is_final,
            "has_scores": (my_actual_points > 0 or opp_points > 0),
            "opponent_name": opp_name,
            "opponent_roster_id": opp_m.roster_id if opp_m else None,
            "my_current_score": my_actual_points,
            "opp_current_score": opp_points,
            "my_projected": lineup_rec.current_projected_total,
            "opp_projected": round(opp_projected, 1),
            "projected_diff": round(lineup_rec.current_projected_total - opp_projected, 1),
            "starter_comparisons": starter_comparisons,
        }

        # Save pending actions to state
        state = AgentState.load()
        formatted_moves = []
        for m in lineup_rec.moves:
            act = LineupChangeAction(
                bench_player_id=m.bench_player_id,
                start_player_id=m.start_player_id,
                slot=m.slot,
                reason=m.reason,
                bench_player_name=m.bench_player_name,
                start_player_name=m.start_player_name,
            )
            state.add_pending_action(act)
            formatted_moves.append({
                "action_id": act.action_id,
                "start_player_id": m.start_player_id,
                "start_player_name": m.start_player_name,
                "bench_player_id": m.bench_player_id,
                "bench_player_name": m.bench_player_name,
                "slot": m.slot,
                "projected_diff": m.projected_diff,
                "reason": m.reason,
            })
        state.save()

        dashboard_res = {
            "team": {
                "roster_id": roster_id,
                "team_name": owner.team_name,
                "owner_name": owner.display_name,
                "wins": user_roster.wins,
                "losses": user_roster.losses,
                "points_for": user_roster.points_for,
                "waiver_position": user_roster.waiver_position,
                "is_faab": is_faab,
                "faab_remaining": faab_rem,
                "faab_budget": league.faab_budget if is_faab else 0,
            },
            "starters": starters_details,
            "bench": bench_details,
            "lineup_optimization": {
                "current_projected": lineup_rec.current_projected_total,
                "optimal_projected": lineup_rec.optimal_projected_total,
                "potential_gain": round(lineup_rec.optimal_projected_total - lineup_rec.current_projected_total, 1),
                "warnings": lineup_rec.warnings,
                "recommended_swaps": formatted_moves,
            },
            "waivers": {
                "is_faab": is_faab,
                "targets": [
                    {
                        "player_id": t.player_id,
                        "name": t.player_name,
                        "position": t.position,
                        "team": t.team,
                        "projected_points": t.projected_points,
                        "recommended_faab": t.recommended_faab if is_faab else 0,
                        "reason": t.reason,
                        "priority_score": round(t.priority_score, 1),
                        "roster_fit": t.roster_fit,
                        "suggested_drop": t.suggested_drop_player_name,
                        "suggested_drop_player_id": t.suggested_drop_player_id,
                        "starter_upgrade_pts": t.starter_upgrade_pts,
                        "is_injury_replacement": t.is_injury_replacement,
                        "is_starter_upgrade": t.is_starter_upgrade,
                    }
                    for t in waiver_rec.targets
                ],
                "drop_candidates": [
                    {
                        "player_id": d.player_id,
                        "name": d.player_name,
                        "position": d.position,
                        "projected_points": d.projected_points,
                        "reason": d.reason,
                        "drop_score": round(d.drop_score, 1),
                        "is_surplus": d.is_surplus,
                    }
                    for d in waiver_rec.drop_candidates
                ],
            },
            "matchup": matchup_info,
            "cached": False,
        }
        ttl = 604800 if is_final else 1800
        self.db.set_cached_query(cache_key, dashboard_res, ttl_seconds=ttl)
        self._send_json(dashboard_res)

    def _handle_team_gm_insights(self, league_id: str, roster_id: int, query: Optional[Dict[str, List[str]]] = None) -> None:
        force_refresh = False
        selected_week_param = None
        if query:
            force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
            if "week" in query:
                try:
                    selected_week_param = int(query["week"][0])
                except (ValueError, TypeError):
                    selected_week_param = None

        if selected_week_param is not None:
            selected_week = selected_week_param
        else:
            nfl_state = self.client.get_nfl_state()
            selected_week = nfl_state.week

        cache_key = f"gm_insights:{league_id}:{roster_id}:week{selected_week}"
        if not force_refresh:
            cached = self.db.get_cached_query(cache_key)
            if cached:
                self._send_json(cached)
                return

        league = self.client.get_league(league_id)
        if not league:
            self._send_error("League not found", 404)
            return

        rosters = self.client.get_rosters(league_id)
        user_roster = next((r for r in rosters if r.roster_id == roster_id), None)
        if not user_roster:
            self._send_error("Team roster not found", 404)
            return

        users = self.client.get_league_users(league_id)
        owner_map = build_roster_owner_map(rosters, users)
        owner = owner_map.get(roster_id, User(user_id="", username=f"team_{roster_id}", display_name=f"Team {roster_id}"))

        all_players = self.get_players()
        matchups = self.client.get_matchups(league_id, selected_week)
        my_m = next((m for m in matchups if m.roster_id == roster_id), None)
        opp_m = find_opponent_matchup(matchups, roster_id)
        opp_owner = owner_map.get(opp_m.roster_id) if opp_m else None
        if opp_m:
            opp_name = opp_owner.team_name if opp_owner else f"Time {opp_m.roster_id}"
        else:
            opp_name = "Sem oponente (Bye / Folga)"

        projections = ProjectionEngine()
        projections.set_player_database(all_players)
        ppr_bonus = float(league.scoring_settings.get("rec", 0.5))

        starting_slots = get_starting_slots(league)
        starters_details = []
        for i, slot in enumerate(starting_slots):
            pid = user_roster.starters[i] if i < len(user_roster.starters) else ""
            if pid and pid in all_players:
                p = all_players[pid]
                proj = projections.get_projection(p, ppr_bonus=ppr_bonus)
                status_desc = p.injury_status or "Ativo"
                if p.injury_status and p.injury_status.upper() == "NA":
                    status_desc = "NA (Inativo / Não Joga)"
                elif p.injury_status and p.injury_status.upper() == "OUT":
                    status_desc = "OUT (Fora / Não Joga)"
                elif p.injury_status and p.injury_status.upper() == "IR":
                    status_desc = "IR (Injured Reserve)"
                starters_details.append({
                    "slot": slot,
                    "name": p.full_name,
                    "team": p.team or "FA",
                    "projected": proj.projected_points,
                    "floor": proj.floor,
                    "ceiling": proj.ceiling,
                    "status": status_desc,
                })
            else:
                starters_details.append({
                    "slot": slot,
                    "name": "[EMPTY]",
                    "team": "",
                    "projected": 0.0,
                    "floor": 0.0,
                    "ceiling": 0.0,
                    "status": "Ativo",
                })

        bench_details = []
        for pid in user_roster.bench_players:
            if pid in all_players:
                p = all_players[pid]
                proj = projections.get_projection(p, ppr_bonus=ppr_bonus)
                status_desc = p.injury_status or "Ativo"
                if p.injury_status and p.injury_status.upper() == "NA":
                    status_desc = "NA (Inativo / Não Joga)"
                elif p.injury_status and p.injury_status.upper() == "OUT":
                    status_desc = "OUT (Fora / Não Joga)"
                elif p.injury_status and p.injury_status.upper() == "IR":
                    status_desc = "IR (Injured Reserve)"
                bench_details.append({
                    "name": p.full_name,
                    "position": p.position,
                    "team": p.team or "FA",
                    "projected": proj.projected_points,
                    "status": status_desc,
                })


        lineup_rec = self.optimizer.optimize(user_roster, league, all_players)
        waiver_rec = self.waiver_optimizer.analyze(user_roster, league, all_players, rosters, top_n=6)

        # Build AgentState for LLMReasoningEngine
        state = AgentState.load()
        state.league = {
            "name": league.name,
            "season": league.season,
            "scoring_type": "Half-PPR (0.5)" if league.scoring_settings.get("rec", 0) == 0.5 else ("PPR (1.0)" if league.scoring_settings.get("rec", 0) == 1.0 else "Standard"),
        }
        state.team = {
            "name": owner.team_name,
            "record": f"{user_roster.wins}-{user_roster.losses}",
            "points_for": user_roster.points_for,
            "waiver_position": user_roster.waiver_position,
            "is_faab": (league.waiver_type == "faab"),
            "faab_remaining": get_faab_remaining(user_roster, league) if league.waiver_type == "faab" else 0,
            "starters": starters_details,
            "bench": bench_details,
        }
        state.injuries = [
            {"name": s["name"], "position": s["slot"], "status": s["status"], "team": s["team"]}
            for s in starters_details if s["status"] != "Ativo"
        ] + [
            {"name": b["name"], "position": b["position"], "status": b["status"], "team": b["team"]}
            for b in bench_details if b["status"] != "Ativo"
        ]
        state.pending_actions = [
            LineupChangeAction(
                bench_player_id=m.bench_player_id,
                start_player_id=m.start_player_id,
                slot=m.slot,
                reason=m.reason,
                bench_player_name=m.bench_player_name,
                start_player_name=m.start_player_name,
            )
            for m in lineup_rec.moves
        ]
        state.free_agents = [
            {
                "name": t.player_name,
                "position": t.position,
                "team": t.team,
                "projected_points": t.projected_points,
                "roster_fit": t.roster_fit,
                "suggested_drop": t.suggested_drop_player_name,
                "reason": t.reason,
                "is_injury_replacement": t.is_injury_replacement,
                "is_starter_upgrade": t.is_starter_upgrade,
                "starter_upgrade_pts": t.starter_upgrade_pts,
            }
            for t in waiver_rec.targets
        ]
        state.drop_candidates = [
            {
                "name": d.player_name,
                "position": d.position,
                "projected_points": d.projected_points,
                "reason": d.reason,
                "drop_score": round(d.drop_score, 1),
                "is_surplus": d.is_surplus,
            }
            for d in waiver_rec.drop_candidates
        ]
        opp_projected = 0.0
        opp_starters_summary = []
        if opp_m:
            for pid in opp_m.starters:
                if pid in all_players:
                    pl = all_players[pid]
                    proj_val = projections.get_projection(pl, ppr_bonus=ppr_bonus).projected_points
                    opp_starters_summary.append(f"{pl.full_name} ({pl.position} - {proj_val:.1f} pts)")
                    opp_projected += proj_val

        state.matchup = {
            "opponent_name": opp_name,
            "opponent_roster_id": opp_m.roster_id if opp_m else None,
            "my_projected": lineup_rec.current_projected_total,
            "opponent_projected": round(opp_projected, 1),
            "projected_diff": round(lineup_rec.current_projected_total - opp_projected, 1),
            "opponent_key_starters": opp_starters_summary[:5],
        }

        try:
            # Analyze using Gemini Flash Reasoner (with automatic fallback to Ollama and heuristic engine)
            analysis_res = self.llm_reasoner.analyze(
                f"Elabore um parecer executivo semanal completo do General Manager para a Rodada {selected_week}.",
                state,
            )

            res = {
                "status": "ok",
                "team_name": owner.team_name,
                "week": selected_week,
                "model_used": analysis_res.get("model_used", "gemini-3.5-flash-lite"),
                "source": analysis_res.get("source", "gemini"),
                "analysis": analysis_res.get("analysis", ""),
                "cached": False,
                "created_at": time.time(),
            }

            self.db.set_cached_query(cache_key, res, ttl_seconds=7200)
            self._send_json(res)
        except Exception as e:
            logger.exception("Error during GM insights generation; falling back to deterministic heuristic.")
            heuristic_res = self.llm_reasoner._heuristic_reasoning(
                f"Parecer Rodada {selected_week}", state
            )
            fallback_res = {
                "status": "ok",
                "team_name": owner.team_name,
                "week": selected_week,
                "model_used": "Motor Heurístico Persone (Local)",
                "source": "heuristic",
                "analysis": heuristic_res.get("analysis", ""),
                "cached": False,
                "created_at": time.time(),
            }
            self._send_json(fallback_res)

    def _handle_team_workflow(self, league_id: str, roster_id: int, day: str) -> None:
        league = self.client.get_league(league_id)
        if not league:
            self._send_error("League not found", 404)
            return

        rosters = self.client.get_rosters(league_id)
        users = self.client.get_league_users(league_id)
        user_roster = next((r for r in rosters if r.roster_id == roster_id), None)
        if not user_roster or not user_roster.owner_id:
            self._send_error("Roster or owner not found", 404)
            return

        planner = WeeklyGMPlanner(self.client, league, user_roster.owner_id)
        all_players = self.get_players()

        if day in ("tuesday", "tue"):
            res = planner.run_tuesday_waiver_report(all_players, rosters)
        elif day in ("thursday", "thu"):
            res = planner.run_thursday_tnf_check(all_players, rosters)
        else:
            matchups = self.client.get_matchups(league_id, self.client.get_nfl_state().week)
            res = planner.run_sunday_lineup_lock(all_players, rosters, matchups, users)

        self._send_json({
            "day": res.day,
            "headline": res.headline,
            "summary_lines": res.summary_lines,
            "proposed_actions": [a.to_dict() for a in res.proposed_actions],
        })


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    try:
        os.umask(0)
    except Exception:
        pass
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, SleeperAgentRequestHandler)
    print(f"[*] Sleeper Agent Web UI running at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down server...")
        httpd.server_close()
