"""Twitter API client using RapidAPI."""

from __future__ import annotations

import httpx

from scorer_twitter.errors import TwitterApiError
from scorer_twitter.types import TwitterApiUser, TwitterSearchResponse
from scorer_utils import get_logger
from scorer_utils.settings import get_settings

log = get_logger("scorer_twitter.client")

RAPIDAPI_HOST = "twitterx-api.p.rapidapi.com"
RAPIDAPI_BASE_URL = f"https://{RAPIDAPI_HOST}"


class TwitterClient:
    """Twitter API client via RapidAPI."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize Twitter client.

        Args:
            api_key: RapidAPI key. If not provided, reads from settings.

        Raises:
            TwitterApiError: If API key is not available.
        """
        if api_key is None:
            settings = get_settings()
            if settings.twitterx_apikey is None:
                raise TwitterApiError.api_key_missing()
            api_key = settings.twitterx_apikey.get_secret_value()

        self._api_key = api_key
        self._client = httpx.Client(
            base_url=RAPIDAPI_BASE_URL,
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": RAPIDAPI_HOST,
            },
            timeout=30.0,
        )

    def search_users(
        self,
        keyword: str,
        *,
        items: int = 20,
        cursor: str | None = None,
    ) -> TwitterSearchResponse:
        """Search for Twitter users by keyword.

        Args:
            keyword: Search keyword.
            items: Number of results per page (default 20).
            cursor: Pagination cursor from previous response.

        Returns:
            TwitterSearchResponse with users and optional next cursor.

        Raises:
            TwitterApiError: On API errors.
        """
        params: dict[str, str | int] = {
            "query": keyword,
            "count": items,
        }
        if cursor:
            params["cursor"] = cursor

        log.info("searching_users", keyword=keyword, items=items, has_cursor=cursor is not None)

        try:
            response = self._client.get("/search/users", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise TwitterApiError.rate_limited() from e
            raise TwitterApiError.network_error(str(e)) from e
        except httpx.RequestError as e:
            raise TwitterApiError.network_error(str(e)) from e

        data = response.json()

        # Parse response with Pydantic validation
        users = [TwitterApiUser.model_validate(u) for u in data.get("users", [])]
        next_cursor = data.get("next_cursor")

        log.info("search_complete", keyword=keyword, users_found=len(users))

        return TwitterSearchResponse(users=users, next_cursor=next_cursor)

    def get_user(self, username: str) -> TwitterApiUser:
        """Get a single user by username.

        Args:
            username: Twitter handle (without @).

        Returns:
            TwitterApiUser object.

        Raises:
            TwitterApiError: If user not found or API error.
        """
        log.info("getting_user", username=username)

        try:
            response = self._client.get(f"/user/{username}")
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise TwitterApiError.user_not_found(username) from e
            if e.response.status_code == 429:
                raise TwitterApiError.rate_limited() from e
            raise TwitterApiError.network_error(str(e)) from e
        except httpx.RequestError as e:
            raise TwitterApiError.network_error(str(e)) from e

        data = response.json()
        return TwitterApiUser.model_validate(data)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> TwitterClient:
        """Enter context manager."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit context manager."""
        self.close()
