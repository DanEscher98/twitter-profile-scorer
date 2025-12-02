"""Twitter/BlueSky API client for profile search.

This package handles querying social media APIs for profiles
based on keywords, with proper pagination and rate limiting.
"""

from search_profiles.client import TwitterClient
from search_profiles.errors import ErrorCode, TwitterApiError
from search_profiles.types import (
    TwitterApiUser,
    TwitterLegacy,
    TwitterProfessional,
    TwitterSearchResponse,
)

__all__ = [
    "ErrorCode",
    "TwitterApiError",
    "TwitterApiUser",
    "TwitterClient",
    "TwitterLegacy",
    "TwitterProfessional",
    "TwitterSearchResponse",
]
