"""Unit tests for fantasy trade analyzer."""

import unittest
from sleeper.models import Player, Roster
from fantasy.trades import TradeAnalyzer


class TestTrades(unittest.TestCase):

    def setUp(self):
        self.roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["p1"],
            players=["p1"],
        )

        self.all_players = {
            "p1": Player("p1", "Old", "Veteran", "RB", search_rank=60),
            "p2": Player("p2", "Elite", "Receiver", "WR", search_rank=5),
            "p_injured": Player("p_injured", "Hurt", "Star", "RB", search_rank=8, injury_status="IR"),
        }

    def test_favorable_trade(self):
        analyzer = TradeAnalyzer()
        eval_res = analyzer.evaluate_trade(
            self.roster,
            give_player_ids=["p1"],
            receive_player_ids=["p2"],
            all_players=self.all_players,
        )

        self.assertEqual(eval_res.verdict, "Favorable")
        self.assertGreater(eval_res.net_projected_diff, 0)
        self.assertTrue(any("RB depth: decreases" in imp for imp in eval_res.roster_impacts))
        self.assertTrue(any("WR depth: increases" in imp for imp in eval_res.roster_impacts))

    def test_high_risk_trade_with_injury(self):
        analyzer = TradeAnalyzer()
        eval_res = analyzer.evaluate_trade(
            self.roster,
            give_player_ids=["p1"],
            receive_player_ids=["p_injured"],
            all_players=self.all_players,
        )

        self.assertEqual(eval_res.verdict, "High Risk")
        self.assertTrue(len(eval_res.risks) > 0)

    def test_player_resolution_by_name(self):
        analyzer = TradeAnalyzer()
        eval_res = analyzer.evaluate_trade(
            self.roster,
            give_player_ids=["Old Veteran"],
            receive_player_ids=["Elite Receiver"],
            all_players=self.all_players,
        )
        self.assertEqual(eval_res.give_players[0].player_id, "p1")
        self.assertEqual(eval_res.receive_players[0].player_id, "p2")
        self.assertGreater(eval_res.give_total_projected, 0)
        self.assertGreater(eval_res.receive_total_projected, 0)

    def test_two_team_context_evaluation(self):
        from sleeper.models import League
        league = League(
            league_id="1",
            name="Test League",
            season="2024",
            sport="nfl",
            status="in_season",
            total_rosters=2,
            roster_positions=["RB", "WR", "BN"],
        )
        partner_roster = Roster(
            roster_id=2,
            owner_id="user2",
            league_id="1",
            starters=["p2"],
            players=["p2"],
        )

        analyzer = TradeAnalyzer()
        eval_res = analyzer.evaluate_trade(
            self.roster,
            give_player_ids=["p1"],
            receive_player_ids=["p2"],
            all_players=self.all_players,
            league=league,
            partner_roster=partner_roster,
        )

        self.assertIsNotNone(eval_res.my_team)
        self.assertIsNotNone(eval_res.partner_team)
        self.assertEqual(eval_res.my_team.roster_id, 1)
        self.assertEqual(eval_res.partner_team.roster_id, 2)
        self.assertGreater(eval_res.win_win_score, 0)
        self.assertIsNotNone(eval_res.feasibility)

    def test_auto_detect_partner_roster(self):
        from sleeper.models import League
        league = League(
            league_id="1",
            name="Test League",
            season="2024",
            sport="nfl",
            status="in_season",
            total_rosters=2,
            roster_positions=["RB", "WR", "BN"],
        )
        partner_roster = Roster(
            roster_id=2,
            owner_id="user2",
            league_id="1",
            starters=["p2"],
            players=["p2"],
        )

        analyzer = TradeAnalyzer()
        # Do not pass partner_roster directly; let analyzer auto-detect from all_rosters!
        eval_res = analyzer.evaluate_trade(
            self.roster,
            give_player_ids=["Old Veteran"],
            receive_player_ids=["Elite Receiver"],
            all_players=self.all_players,
            league=league,
            all_rosters=[self.roster, partner_roster],
        )

        self.assertIsNotNone(eval_res.partner_team)
        self.assertEqual(eval_res.partner_team.roster_id, 2)
        self.assertIn(eval_res.give_players[0].player_id, ["p1"])
        self.assertIn(eval_res.receive_players[0].player_id, ["p2"])


if __name__ == "__main__":
    unittest.main()

