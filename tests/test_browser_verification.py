"""Unit tests for browser action verification routines."""

import unittest
from sleeper.models import Player, Roster, Transaction
from agent.actions import LineupChangeAction, WaiverClaimAction
from browser.verification import ActionVerifier


class TestBrowserVerification(unittest.TestCase):

    def setUp(self):
        self.players = {
            "p1": Player("p1", "Starter", "One", "RB"),
            "p2": Player("p2", "Bench", "Two", "WR"),
        }

    def test_lineup_verification_success(self):
        action = LineupChangeAction(
            bench_player_id="p1",
            start_player_id="p2",
            slot="FLEX",
            reason="Matchup upgrade",
        )

        roster_after = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["p2"],
            players=["p1", "p2"],
        )

        res = ActionVerifier.verify_lineup_change(action, roster_after, self.players)
        self.assertTrue(res.success)
        self.assertEqual(res.verified_state, "Verified Active")
        self.assertIn("Moved Bench Two into starting lineup", res.diff_summary[0])

    def test_lineup_verification_failure(self):
        action = LineupChangeAction(
            bench_player_id="p1",
            start_player_id="p2",
            slot="FLEX",
            reason="Test",
        )

        # Roster after mutation still has p1 as starter, p2 on bench
        roster_after = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["p1"],
            players=["p1", "p2"],
        )

        res = ActionVerifier.verify_lineup_change(action, roster_after, self.players)
        self.assertFalse(res.success)
        self.assertIn("was not found in active starters", res.error)

    def test_waiver_claim_verification(self):
        action = WaiverClaimAction(
            add_player_id="p2",
            drop_player_id="p1",
            faab=17,
            reason="Waiver bid",
        )

        tx = Transaction(
            transaction_id="tx_123",
            type="waiver",
            status="pending",
            status_updated=1000,
            roster_ids=[1],
            adds={"p2": 1},
            drops={"p1": 1},
            settings={"waiver_bid": 17},
        )

        res = ActionVerifier.verify_waiver_claim(action, [tx], self.players)
        self.assertTrue(res.success)
        self.assertIn("tx_123", res.diff_summary[3])


if __name__ == "__main__":
    unittest.main()
