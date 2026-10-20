"""Unit tests for lineup optimizer and start/sit logic."""

import unittest
from sleeper.models import League, Player, Roster
from fantasy.lineup import LineupOptimizer
from fantasy.projections import ProjectionEngine


class TestLineupOptimizer(unittest.TestCase):

    def setUp(self):
        self.league = League(
            league_id="1",
            name="Test League",
            season="2024",
            sport="nfl",
            status="active",
            total_rosters=10,
            roster_positions=["QB", "RB", "WR", "FLEX", "BN"],
        )

        self.players = {
            "qb1": Player(player_id="qb1", first_name="Patrick", last_name="Mahomes", position="QB", search_rank=1),
            "rb1": Player(player_id="rb1", first_name="Christian", last_name="McCaffrey", position="RB", search_rank=1),
            "rb_bench": Player(player_id="rb_bench", first_name="Bench", last_name="Runner", position="RB", search_rank=15),
            "wr1": Player(player_id="wr1", first_name="Justin", last_name="Jefferson", position="WR", search_rank=2),
            "wr_injured": Player(player_id="wr_injured", first_name="Hurt", last_name="Receiver", position="WR", injury_status="Out", search_rank=20),
            "flex_stud": Player(player_id="flex_stud", first_name="Stud", last_name="Flexer", position="WR", search_rank=10),
        }

    def test_injured_starter_is_flagged_and_replaced(self):
        # User currently has wr_injured in starting lineup, flex_stud on bench
        roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["qb1", "rb1", "wr_injured", "rb_bench"],
            players=["qb1", "rb1", "wr_injured", "rb_bench", "flex_stud"],
        )

        optimizer = LineupOptimizer()
        rec = optimizer.optimize(roster, self.league, self.players)

        # Confirm warning generated for injured player
        self.assertTrue(any("Hurt Receiver" in w for w in rec.warnings))

        # Confirm optimal projected points higher than current
        self.assertGreater(rec.optimal_projected_total, rec.current_projected_total)

        # Confirm swap recommendation
        self.assertTrue(len(rec.moves) > 0)
        self.assertTrue(any(m.bench_player_id == "wr_injured" for m in rec.moves))

    def test_inactive_na_player_is_not_recommended_as_starter(self):
        # A stud player with high search rank but injury_status="NA" on bench
        players = dict(self.players)
        players["rb_na"] = Player(
            player_id="rb_na",
            first_name="Josh",
            last_name="Jacobs",
            position="RB",
            search_rank=5,
            injury_status="NA",
        )
        self.assertTrue(players["rb_na"].is_injured)
        self.assertTrue(players["rb_na"].is_inactive)

        engine = ProjectionEngine()
        engine.set_player_database(players)
        proj = engine.get_projection(players["rb_na"])
        self.assertEqual(proj.projected_points, 0.0)

        # Roster with healthy starter rb1, scrub on bench rb_bench, and inactive stud rb_na
        roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["qb1", "rb1", "wr1", "flex_stud"],
            players=["qb1", "rb1", "wr1", "flex_stud", "rb_bench", "rb_na"],
        )

        optimizer = LineupOptimizer()
        rec = optimizer.optimize(roster, self.league, players)

        # Confirm inactive player rb_na is NEVER recommended to be swapped in
        for move in rec.moves:
            self.assertNotEqual(move.start_player_id, "rb_na")


if __name__ == "__main__":
    unittest.main()

