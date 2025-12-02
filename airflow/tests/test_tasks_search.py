"""Tests for search task module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from db import Platform


class TestSearchProfilesForKeyword:
    """Tests for search_profiles_for_keyword function."""

    @patch("tasks.search.TwitterClient")
    def test_successful_search(
        self,
        mock_client_class: MagicMock,
        sample_twitter_user: MagicMock,
    ) -> None:
        """Test successful profile search."""
        from search_profiles import TwitterSearchResponse

        from tasks.search import search_profiles_for_keyword

        # Setup mock
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_users.return_value = TwitterSearchResponse(
            users=[sample_twitter_user],
            next_cursor="next_cursor_123",
        )
        mock_client_class.return_value = mock_client

        result = search_profiles_for_keyword(
            "researcher",
            items=20,
            cursor=None,
            page=0,
            platform=Platform.TWITTER,
        )

        assert result.keyword == "researcher"
        assert len(result.users) == 1
        assert result.next_cursor == "next_cursor_123"
        assert result.page == 0

    def test_unsupported_platform(self) -> None:
        """Test search with unsupported platform."""
        from tasks.search import search_profiles_for_keyword

        result = search_profiles_for_keyword(
            "researcher",
            platform=Platform.BLUESKY,
        )

        assert result.users == []
        assert result.next_cursor is None


class TestProcessProfiles:
    """Tests for process_profiles function."""

    def test_process_profiles(self, sample_twitter_user: MagicMock) -> None:
        """Test processing profiles with HAS computation."""
        from tasks.search import SearchResult, process_profiles

        search_result = SearchResult(
            users=[sample_twitter_user],
            next_cursor=None,
            keyword="researcher",
            platform=Platform.TWITTER,
            page=0,
        )

        processed = process_profiles(search_result)

        assert len(processed) == 1
        assert processed[0].user.rest_id == sample_twitter_user.rest_id
        assert processed[0].has_result.score > 0
        assert processed[0].keyword == "researcher"

    def test_process_empty_result(self) -> None:
        """Test processing empty search result."""
        from tasks.search import SearchResult, process_profiles

        search_result = SearchResult(
            users=[],
            next_cursor=None,
            keyword="researcher",
            platform=Platform.TWITTER,
            page=0,
        )

        processed = process_profiles(search_result)

        assert processed == []
