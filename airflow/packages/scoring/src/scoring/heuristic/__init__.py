"""Human Authenticity Score (HAS) algorithm.

Computes a 0-1 score indicating likelihood that a profile is human,
based on engagement patterns, account age indicators, and verification status.
"""

from scoring.heuristic.scorer import compute_has
from scoring.heuristic.types import HASResult, HASScoreBreakdown

__all__ = ["HASResult", "HASScoreBreakdown", "compute_has"]
