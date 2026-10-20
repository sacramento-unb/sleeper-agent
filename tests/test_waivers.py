"""Unit tests for waiver optimizer and FAAB recommendations."""

import unittest
from sleeper.models import League, Player, Roster
from fantasy.waivers import WaiverOptimizer


class TestWaivers(unittest.TestCase):

    def setUp(self):
        self.league = League(
            league_id="1",
            name="Test League",
            season="2024",
            sport="nfl",
            status="active",
            total_rosters=10,
            settings={"waiver_budget": 100},
        )

        self.user_roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["p1", "p2"],
            players=["p1", "p2", "bench_scrub"],
            settings={"waiver_budget_used": 20},  # $80 FAAB remaining
        )

        self.other_roster = Roster(
            roster_id=2,
            owner_id="user2",
            league_id="1",
            starters=["p3"],
            players=["p3"],
        )

        self.all_players = {
            "p1": Player("p1", "Starter", "One", "RB", search_rank=5),
            "p2": Player("p2", "Starter", "Two", "WR", search_rank=8),
            "bench_scrub": Player("bench_scrub", "Deep", "Reserve", "RB", search_rank=200, depth_chart_order=4),
            "p3": Player("p3", "Other", "Guy", "QB", search_rank=10),
            "waiver_gem": Player("waiver_gem", "Emerging", "Star", "RB", search_rank=15, depth_chart_order=1),
            "waiver_decent": Player("waiver_decent", "Decent", "Flex", "WR", search_rank=40, depth_chart_order=2),
        }

    def test_waiver_analysis(self):
        optimizer = WaiverOptimizer()
        rec = optimizer.analyze(
            self.user_roster,
            self.league,
            self.all_players,
            [self.user_roster, self.other_roster],
            top_n=3,
        )

        self.assertEqual(rec.faab_remaining, 80)
        self.assertTrue(len(rec.targets) >= 1)
        top_target = rec.targets[0]
        self.assertEqual(top_target.player_id, "waiver_gem")
        self.assertGreater(top_target.recommended_faab, 0)
        self.assertLessEqual(top_target.recommended_faab, 80)

        # Drop candidate should be bench_scrub
        self.assertTrue(any(d.player_id == "bench_scrub" for d in rec.drop_candidates))

    def test_retired_players_excluded_from_waivers(self):
        # Tom Brady, Drew Brees, Ben Roethlisberger representations
        retired_players = {
            "p1": Player("p1", "Starter", "One", "RB", search_rank=5),
            "167": Player("167", "Tom", "Brady", "QB", search_rank=74, active=True, team=None),
            "289": Player("289", "Drew", "Brees", "QB", search_rank=76, active=True, team=None),
            "138": Player("138", "Ben", "Roethlisberger", "QB", search_rank=176, active=True, team="PIT", news_updated=1643296817250),
            "waiver_active_qb": Player("waiver_active_qb", "Active", "QB", "QB", search_rank=80, depth_chart_order=1, team="DEN"),
        }

        optimizer = WaiverOptimizer()
        rec = optimizer.analyze(
            self.user_roster,
            self.league,
            retired_players,
            [self.user_roster, self.other_roster],
            top_n=5,
        )

        target_ids = [t.player_id for t in rec.targets]
        self.assertNotIn("167", target_ids)
        self.assertNotIn("289", target_ids)
        self.assertNotIn("138", target_ids)
        self.assertIn("waiver_active_qb", target_ids)

    def test_roster_injury_prioritizes_injured_position(self):
        # Team has 2 elite QBs, but starting RB is NA (Not Active)
        players = {
            "qb1": Player("qb1", "Patrick", "Mahomes", "QB", search_rank=2, depth_chart_order=1, team="KC"),
            "qb2": Player("qb2", "Jalen", "Hurts", "QB", search_rank=4, depth_chart_order=1, team="PHI"),
            "rb_inactive": Player("rb_inactive", "Josh", "Jacobs", "RB", search_rank=10, depth_chart_order=1, team="GB", injury_status="NA"),
            "rb_waiver": Player("rb_waiver", "Emerging", "RB", "RB", search_rank=35, depth_chart_order=1, team="LAC"),
            "qb_waiver": Player("qb_waiver", "Streaming", "QB", "QB", search_rank=25, depth_chart_order=1, team="MIN"),
        }

        injured_roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["qb1", "rb_inactive"],
            players=["qb1", "qb2", "rb_inactive"],
        )

        optimizer = WaiverOptimizer()
        rec = optimizer.analyze(
            injured_roster,
            self.league,
            players,
            [injured_roster],
            top_n=2,
        )

        # RB waiver target must be #1 priority to replace the inactive starter, despite lower raw rank than QB
        self.assertEqual(rec.targets[0].player_id, "rb_waiver")
        self.assertTrue(rec.targets[0].is_injury_replacement)
        self.assertIn("Josh Jacobs", rec.targets[0].reason)

    def test_roster_surplus_determines_drop_candidate(self):
        # Team has 3 QBs and only 1 backup RB. The 3rd QB should be suggested for drop over the only backup RB.
        players = {
            "qb1": Player("qb1", "Star", "QB1", "QB", search_rank=5),
            "qb2": Player("qb2", "Backup", "QB2", "QB", search_rank=20),
            "qb3": Player("qb3", "Surplus", "QB3", "QB", search_rank=60),
            "rb1": Player("rb1", "Star", "RB", "RB", search_rank=10),
            "rb_backup": Player("rb_backup", "Lone", "BackupRB", "RB", search_rank=85),
            "free_rb": Player("free_rb", "Waiver", "Target", "RB", search_rank=30, depth_chart_order=1, team="NE"),
        }

        surplus_roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["qb1", "rb1"],
            players=["qb1", "rb1", "qb2", "qb3", "rb_backup"],
        )

        optimizer = WaiverOptimizer()
        rec = optimizer.analyze(
            surplus_roster,
            self.league,
            players,
            [surplus_roster],
            top_n=1,
        )

        # Drop candidates should rank the surplus QB ahead of the lone backup RB
        drop_ids = [d.player_id for d in rec.drop_candidates]
        self.assertEqual(drop_ids[0], "qb3")
        self.assertTrue(rec.drop_candidates[0].is_surplus)
        self.assertIn("Excesso", rec.drop_candidates[0].reason)



if __name__ == "__main__":
    unittest.main()

