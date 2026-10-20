"""Unit tests for Sleeper API client and models."""

import unittest
from unittest.mock import patch, MagicMock
from sleeper.client import SleeperClient
from sleeper.models import League, Roster, User, Matchup, Player, Transaction, NFLState
from sleeper.rosters import find_user_roster, build_roster_owner_map, get_faab_remaining
from sleeper.matchups import find_opponent_matchup
from sleeper.leagues import get_starting_slots, get_ppr_format


class TestSleeperClient(unittest.TestCase):

    def setUp(self):
        self.client = SleeperClient(cache_dir=".test_cache")

    @patch.object(SleeperClient, "_get")
    def test_get_user(self, mock_get):
        mock_get.return_value = {
            "user_id": "12345",
            "username": "testuser",
            "display_name": "Test User",
            "metadata": {"team_name": "Gridiron Heroes"},
        }
        user = self.client.get_user("testuser")
        self.assertIsNotNone(user)
        self.assertEqual(user.user_id, "12345")
        self.assertEqual(user.team_name, "Gridiron Heroes")

    @patch.object(SleeperClient, "_get")
    def test_get_league(self, mock_get):
        mock_get.return_value = {
            "league_id": "99999",
            "name": "Super League",
            "season": "2024",
            "sport": "nfl",
            "status": "in_season",
            "total_rosters": 12,
            "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "IR"],
            "settings": {"waiver_type": 2, "waiver_budget": 100, "playoff_week_start": 15},
            "scoring_settings": {"rec": 0.5, "pass_td": 4.0},
        }
        league = self.client.get_league("99999")
        self.assertIsNotNone(league)
        self.assertEqual(league.name, "Super League")
        self.assertEqual(league.waiver_type, "faab")
        self.assertEqual(league.faab_budget, 100)
        self.assertEqual(get_ppr_format(league), "Half PPR (0.5)")
        self.assertEqual(len(get_starting_slots(league)), 9)

    @patch.object(SleeperClient, "_get")
    def test_roster_and_owner_helpers(self, mock_get):
        mock_get.return_value = [
            {
                "roster_id": 1,
                "owner_id": "12345",
                "starters": ["101", "102"],
                "players": ["101", "102", "103"],
                "reserve": ["104"],
                "settings": {"wins": 3, "losses": 1, "waiver_budget_used": 27},
            },
            {
                "roster_id": 2,
                "owner_id": "67890",
                "starters": ["201", "202"],
                "players": ["201", "202"],
                "settings": {"wins": 1, "losses": 3, "waiver_budget_used": 10},
            }
        ]
        rosters = self.client.get_rosters("99999")
        self.assertEqual(len(rosters), 2)

        user_roster = find_user_roster(rosters, "12345")
        self.assertIsNotNone(user_roster)
        self.assertEqual(user_roster.roster_id, 1)
        self.assertEqual(user_roster.bench_players, ["103"])

        mock_league = League(
            league_id="99999",
            name="Test",
            season="2024",
            sport="nfl",
            status="active",
            total_rosters=12,
            settings={"waiver_budget": 100},
        )
        self.assertEqual(get_faab_remaining(user_roster, mock_league), 73)

    def test_find_opponent_matchup(self):
        matchups = [
            Matchup(matchup_id=1, roster_id=1, week=1, points=112.5),
            Matchup(matchup_id=1, roster_id=4, week=1, points=105.0),
            Matchup(matchup_id=2, roster_id=2, week=1, points=98.0),
            Matchup(matchup_id=2, roster_id=3, week=1, points=95.0),
            Matchup(matchup_id=3, roster_id=5, week=1, points=80.0), # Bye matchup
        ]
        # Test with int roster_id
        opp = find_opponent_matchup(matchups, 1)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.roster_id, 4)
        self.assertEqual(opp.points, 105.0)

        # Test with Matchup object (regression test for bug)
        my_m = matchups[0]
        opp_by_matchup = find_opponent_matchup(matchups, my_m)
        self.assertIsNotNone(opp_by_matchup)
        self.assertEqual(opp_by_matchup.roster_id, 4)

        # Test with Roster object
        mock_r = Roster(roster_id=1, owner_id="u1", league_id="l1")
        opp_by_roster = find_opponent_matchup(matchups, mock_r)
        self.assertIsNotNone(opp_by_roster)
        self.assertEqual(opp_by_roster.roster_id, 4)

        # Test bye week / no opponent returns None
        opp_bye = find_opponent_matchup(matchups, 5)
        self.assertIsNone(opp_bye)


if __name__ == "__main__":
    unittest.main()
