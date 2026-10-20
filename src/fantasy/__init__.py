"""Fantasy Football decision and analysis engine."""

from fantasy.scoring import calculate_fantasy_points, get_default_scoring_rules
from fantasy.projections import ProjectionEngine
from fantasy.lineup import LineupOptimizer, LineupRecommendation
from fantasy.waivers import WaiverOptimizer, WaiverRecommendation
from fantasy.trades import TradeAnalyzer, TradeEvaluation

__all__ = [
    "calculate_fantasy_points",
    "get_default_scoring_rules",
    "ProjectionEngine",
    "LineupOptimizer",
    "LineupRecommendation",
    "WaiverOptimizer",
    "WaiverRecommendation",
    "TradeAnalyzer",
    "TradeEvaluation",
]
