"""Unit tests for Web API endpoints and server handlers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

from sleeper.models import League, Matchup, Player, Roster, User
from sleeper_agent.web import SleeperAgentRequestHandler
from storage.db import Database, ADMIN_EMAIL


class DummyServer:
    pass


class TestWebAPI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db = Database(Path(self.temp_dir.name) / "test.db")

        self.mock_league = League(
            league_id="12345",
            name="Persone Premier League",
            season="2024",
            sport="nfl",
            status="in_season",
            total_rosters=2,
            roster_positions=["QB", "RB", "WR", "FLEX", "BN"],
            settings={"waiver_budget": 100, "waiver_type": 2},
        )

        self.mock_user = User(
            user_id="user_1",
            username="persone_user",
            display_name="Persone GM",
            metadata={"team_name": "Persone FC"},
        )

        self.mock_roster1 = Roster(
            roster_id=1,
            owner_id="user_1",
            league_id="12345",
            starters=["p_qb", "p_rb", "p_wr", "p_bench"],
            players=["p_qb", "p_rb", "p_wr", "p_bench", "p_fa"],
            settings={"wins": 3, "losses": 1, "fpts": 450, "waiver_budget_used": 15},
        )

        self.mock_roster2 = Roster(
            roster_id=2,
            owner_id="user_2",
            league_id="12345",
            starters=["p_opp"],
            players=["p_opp"],
            settings={"wins": 2, "losses": 2, "fpts": 410, "waiver_budget_used": 20},
        )

        self.mock_players = {
            "p_qb": Player("p_qb", "Patrick", "Mahomes", "QB", search_rank=1),
            "p_rb": Player("p_rb", "Bijan", "Robinson", "RB", search_rank=3),
            "p_wr": Player("p_wr", "CeeDee", "Lamb", "WR", search_rank=2),
            "p_bench": Player("p_bench", "Bench", "Player", "RB", search_rank=80),
            "p_fa": Player("p_fa", "Free", "Agent", "WR", search_rank=15),
            "p_opp": Player("p_opp", "Opponent", "Star", "QB", search_rank=5),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_handler(self, path: str, method: str = "GET", body: dict = None, authenticated: bool = True):
        handler = SleeperAgentRequestHandler.__new__(SleeperAgentRequestHandler)
        handler.db = self.test_db
        handler.path = path
        handler.command = method
        handler.headers = {
            "Content-Length": str(len(json.dumps(body))) if body else "0",
            "Authorization": "Bearer test-mock-token" if authenticated else "",
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8") if body else b"")
        handler.wfile = BytesIO()

        if authenticated:
            handler._get_authenticated_user = lambda: {
                "token": "test-mock-token",
                "email": ADMIN_EMAIL,
                "is_admin": True,
            }
        else:
            handler._get_authenticated_user = lambda: None

        # Mock send_response & send_header
        handler._headers_buffer = []
        handler.status_code = None

        def mock_send_response(code):
            handler.status_code = code

        def mock_send_header(keyword, value):
            handler._headers_buffer.append(f"{keyword}: {value}")

        def mock_end_headers():
            pass

        handler.send_response = mock_send_response
        handler.send_header = mock_send_header
        handler.end_headers = mock_end_headers

        return handler

    def test_health_check(self):
        handler = self._create_handler("/api/health", authenticated=False)
        handler.do_GET()
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(data["status"], "healthy")

    def test_config_endpoint(self):
        handler = self._create_handler("/api/config", authenticated=False)
        handler.do_GET()
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("configured_league_id", data)

    def test_unauthenticated_access_denied(self):
        handler = self._create_handler("/api/league/12345", authenticated=False)
        handler.do_GET()
        self.assertEqual(handler.status_code, 401)

    @patch.object(SleeperAgentRequestHandler, "get_players")
    def test_league_overview(self, mock_get_players):
        mock_get_players.return_value = self.mock_players
        client = SleeperAgentRequestHandler.client

        with patch.object(client, "get_league", return_value=self.mock_league), \
             patch.object(client, "get_rosters", return_value=[self.mock_roster1, self.mock_roster2]), \
             patch.object(client, "get_league_users", return_value=[self.mock_user]):

            handler = self._create_handler("/api/league/12345", authenticated=True)
            handler.do_GET()

            self.assertEqual(handler.status_code, 200)
            data = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["league"]["name"], "Persone Premier League")
            self.assertEqual(len(data["teams"]), 2)
            self.assertEqual(data["teams"][0]["team_name"], "Persone FC")

    @patch.object(SleeperAgentRequestHandler, "get_players")
    def test_team_dashboard(self, mock_get_players):
        mock_get_players.return_value = self.mock_players
        client = SleeperAgentRequestHandler.client

        with patch.object(client, "get_league", return_value=self.mock_league), \
             patch.object(client, "get_rosters", return_value=[self.mock_roster1, self.mock_roster2]), \
             patch.object(client, "get_league_users", return_value=[self.mock_user]), \
             patch.object(client, "get_matchups", return_value=[
                 Matchup(matchup_id=1, roster_id=1, week=1, points=110.0),
                 Matchup(matchup_id=1, roster_id=2, week=1, points=95.0),
             ]):

            handler = self._create_handler("/api/league/12345/team/1/dashboard", authenticated=True)
            handler.do_GET()

            self.assertEqual(handler.status_code, 200)
            data = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["team"]["team_name"], "Persone FC")
            self.assertEqual(data["team"]["faab_remaining"], 85)
            self.assertTrue(len(data["starters"]) >= 1)
            self.assertIn("lineup_optimization", data)
            self.assertIn("waivers", data)
            self.assertIn("matchup", data)

    def test_auth_first_access_and_login_flow(self):
        # 1. First Access for admin
        handler = self._create_handler(
            "/api/auth/first-access",
            method="POST",
            body={"email": ADMIN_EMAIL, "password": "newpassword123"},
            authenticated=False,
        )
        handler.do_POST()
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertIn("session_token", data)

        # 2. Login with correct password
        handler2 = self._create_handler(
            "/api/auth/login",
            method="POST",
            body={"email": ADMIN_EMAIL, "password": "newpassword123"},
            authenticated=False,
        )
        handler2.do_POST()
        self.assertEqual(handler2.status_code, 200)
        data2 = json.loads(handler2.wfile.getvalue().decode("utf-8"))
        self.assertEqual(data2["email"], ADMIN_EMAIL)
        self.assertTrue(data2["is_admin"])

        # 3. Login with wrong password
        handler3 = self._create_handler(
            "/api/auth/login",
            method="POST",
            body={"email": ADMIN_EMAIL, "password": "wrongpassword"},
            authenticated=False,
        )
        handler3.do_POST()
        self.assertEqual(handler3.status_code, 400)

    def test_admin_permissions(self):
        # Admin can access admin routes
        admin_handler = self._create_handler("/api/admin/users", authenticated=True)
        admin_handler._get_authenticated_user = lambda: {
            "token": "tok1",
            "email": ADMIN_EMAIL,
            "is_admin": True,
        }
        admin_handler.do_GET()
        self.assertEqual(admin_handler.status_code, 200)

        # Non-admin cannot access admin routes (403 Forbidden)
        user_handler = self._create_handler("/api/admin/users", authenticated=True)
        user_handler._get_authenticated_user = lambda: {
            "token": "tok2",
            "email": "normal_user@persone.com",
            "is_admin": False,
        }
        user_handler.do_GET()
        self.assertEqual(user_handler.status_code, 403)

    def test_gm_insights_endpoint(self):
        with patch.object(SleeperAgentRequestHandler.client, "get_league", return_value=self.mock_league), \
             patch.object(SleeperAgentRequestHandler.client, "get_rosters", return_value=[self.mock_roster1, self.mock_roster2]), \
             patch.object(SleeperAgentRequestHandler.client, "get_league_users", return_value=[self.mock_user]), \
             patch.object(SleeperAgentRequestHandler, "get_players", return_value=self.mock_players), \
             patch.object(SleeperAgentRequestHandler.client, "get_nfl_state", return_value=MagicMock(week=1)), \
             patch.object(SleeperAgentRequestHandler.client, "get_matchups", return_value=[
                 Matchup(matchup_id=1, roster_id=1, week=1, points=105.0),
                 Matchup(matchup_id=1, roster_id=2, week=1, points=95.0),
             ]):

            handler = self._create_handler("/api/league/12345/team/1/gm-insights", authenticated=True)
            handler.do_GET()

            self.assertEqual(handler.status_code, 200)
            data = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertIn("analysis", data)
            self.assertNotIn("contra **Bye**", data["analysis"])
            self.assertNotIn("contra **Sem oponente", data["analysis"])
            self.assertIn("Team 2", data["analysis"])
            self.assertIn("model_used", data)
            self.assertIn("source", data)

    def test_team_intelligence_waiver_enrichment(self):
        with patch.object(SleeperAgentRequestHandler.client, "get_league", return_value=self.mock_league), \
             patch.object(SleeperAgentRequestHandler.client, "get_rosters", return_value=[self.mock_roster1, self.mock_roster2]), \
             patch.object(SleeperAgentRequestHandler.client, "get_league_users", return_value=[self.mock_user]), \
             patch.object(SleeperAgentRequestHandler, "get_players", return_value=self.mock_players), \
             patch.object(SleeperAgentRequestHandler.client, "get_nfl_state", return_value=MagicMock(week=1)), \
             patch.object(SleeperAgentRequestHandler.client, "get_matchups", return_value=[
                 Matchup(matchup_id=1, roster_id=1, week=1, points=105.0),
                 Matchup(matchup_id=1, roster_id=2, week=1, points=95.0),
             ]):

            handler = self._create_handler("/api/league/12345/team/1/dashboard", authenticated=True)
            handler.do_GET()

            self.assertEqual(handler.status_code, 200)
            data = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertIn("waivers", data)
            waivers = data["waivers"]
            self.assertIn("targets", waivers)
            self.assertIn("drop_candidates", waivers)
            if waivers["targets"]:
                first_target = waivers["targets"][0]
                self.assertIn("roster_fit", first_target)
                self.assertIn("priority_score", first_target)
                self.assertIn("suggested_drop", first_target)
                self.assertIn("is_injury_replacement", first_target)
                self.assertIn("is_starter_upgrade", first_target)
            if waivers["drop_candidates"]:
                first_drop = waivers["drop_candidates"][0]
                self.assertIn("drop_score", first_drop)
                self.assertIn("is_surplus", first_drop)

    def test_trade_evaluation_endpoint_names_and_two_team(self):
        with patch.object(SleeperAgentRequestHandler.client, "get_league", return_value=self.mock_league), \
             patch.object(SleeperAgentRequestHandler.client, "get_rosters", return_value=[self.mock_roster1, self.mock_roster2]), \
             patch.object(SleeperAgentRequestHandler.client, "get_league_users", return_value=[self.mock_user]), \
             patch.object(SleeperAgentRequestHandler, "get_players", return_value=self.mock_players):

            handler = self._create_handler(
                "/api/league/12345/team/1/trade",
                method="POST",
                body={"give": ["Patrick Mahomes"], "receive": ["Opponent Star"]},
                authenticated=True,
            )
            handler.do_POST()

            self.assertEqual(handler.status_code, 200)
            data = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertIn("verdict", data)
            self.assertIn("give_players", data)
            self.assertIn("receive_players", data)
            self.assertIn("my_team", data)
            self.assertIn("partner_team", data)
            self.assertIn("win_win_score", data)
            self.assertIn("feasibility", data)
            self.assertGreater(data["give_total_projected"], 0)
            self.assertGreater(data["receive_total_projected"], 0)
            self.assertEqual(data["give_players"][0]["name"], "Patrick Mahomes")
            self.assertEqual(data["receive_players"][0]["name"], "Opponent Star")


if __name__ == "__main__":
    unittest.main()


