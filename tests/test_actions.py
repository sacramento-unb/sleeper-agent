"""Unit tests for structured actions and safety policy enforcement."""

import unittest
from sleeper.models import League, Player, Roster
from agent.actions import Action, ActionType, LineupChangeAction, WaiverClaimAction
from agent.policies import SafetyPolicy, PolicyViolationError


class TestActionsAndPolicies(unittest.TestCase):

    def setUp(self):
        self.league = League(
            league_id="1",
            name="Test",
            season="2024",
            sport="nfl",
            status="active",
            total_rosters=10,
            settings={"waiver_budget": 100},
        )

        self.roster = Roster(
            roster_id=1,
            owner_id="user1",
            league_id="1",
            starters=["p1"],
            players=["p1", "p2"],
            settings={"waiver_budget_used": 70},  # $30 remaining
        )

        self.all_players = {
            "p1": Player("p1", "Player", "One", "RB"),
            "p2": Player("p2", "Player", "Two", "WR"),
            "p3": Player("p3", "Player", "Three", "TE"),
        }

    def test_unapproved_action_blocks_execution(self):
        action = LineupChangeAction(
            bench_player_id="p1",
            start_player_id="p2",
            slot="FLEX",
            reason="Better projection",
        )
        self.assertTrue(action.requires_approval)
        self.assertFalse(action.approved)

        with self.assertRaises(PolicyViolationError):
            SafetyPolicy.check_approval(action)

        # Once approved, check passes
        action.approved = True
        SafetyPolicy.check_approval(action)

    def test_faab_overspend_validation_fails(self):
        # Remaining budget is $30, attempting to bid $40
        action = WaiverClaimAction(
            add_player_id="p3",
            drop_player_id="p2",
            faab=40,
            reason="Need TE",
        )
        is_valid, msg = SafetyPolicy.validate_action(action, self.roster, self.league, self.all_players)
        self.assertFalse(is_valid)
        self.assertIn("exceeds remaining budget", msg)

    def test_start_unrostered_player_fails(self):
        # Attempting to start p3 who is not on user's roster
        action = LineupChangeAction(
            bench_player_id="p1",
            start_player_id="p3",
            slot="FLEX",
            reason="Test",
        )
        is_valid, msg = SafetyPolicy.validate_action(action, self.roster, self.league, self.all_players)
        self.assertFalse(is_valid)
        self.assertIn("not on your roster", msg)

    def test_serialization(self):
        action = WaiverClaimAction(
            add_player_id="p3",
            drop_player_id="p2",
            faab=15,
            reason="Top waiver pickup",
            confidence=0.88,
        )
        d = action.to_dict()
        self.assertEqual(d["action"], "waiver_claim")
        self.assertEqual(d["faab"], 15)

        restored = Action.from_dict(d)
        self.assertEqual(restored.action_type, ActionType.WAIVER_CLAIM)
        self.assertEqual(restored.params["faab"], 15)
        self.assertEqual(restored.confidence, 0.88)


if __name__ == "__main__":
    unittest.main()
