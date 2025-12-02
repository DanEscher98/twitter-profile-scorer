"""Tests for keyword task module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from db import Platform


class TestGetValidKeywords:
    """Tests for get_valid_keywords function."""

    @patch("tasks.keywords.get_session")
    def test_empty_keywords(self, mock_get_session: MagicMock) -> None:
        """Test when no valid keywords exist."""
        from tasks.keywords import get_valid_keywords

        mock_session = MagicMock()
        query_result = mock_session.query.return_value
        query_result.filter.return_value.order_by.return_value.all.return_value = []
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = get_valid_keywords(5, Platform.TWITTER)

        assert result == []

    @patch("tasks.keywords.get_session")
    @patch("tasks.keywords._filter_by_pagination")
    def test_valid_keywords_found(
        self,
        mock_filter: MagicMock,
        mock_get_session: MagicMock,
    ) -> None:
        """Test when valid keywords exist."""
        from tasks.keywords import get_valid_keywords

        # Setup mocks
        mock_session = MagicMock()
        mock_keywords = [("researcher",), ("scientist",), ("professor",)]
        query_result = mock_session.query.return_value
        query_result.filter.return_value.order_by.return_value.all.return_value = mock_keywords
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_filter.return_value = ["researcher", "scientist"]

        result = get_valid_keywords(2, Platform.TWITTER)

        assert len(result) == 2


class TestGetPaginationState:
    """Tests for get_pagination_state function."""

    @patch("tasks.keywords.get_session")
    def test_no_previous_search(self, mock_get_session: MagicMock) -> None:
        """Test when no previous search exists."""
        from tasks.keywords import get_pagination_state

        mock_session = MagicMock()
        query_result = mock_session.query.return_value
        query_result.filter.return_value.order_by.return_value.first.return_value = None
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        cursor, page = get_pagination_state("researcher", Platform.TWITTER)

        assert cursor is None
        assert page == 0

    @patch("tasks.keywords.get_session")
    def test_with_previous_search(
        self,
        mock_get_session: MagicMock,
        sample_api_search_usage: MagicMock,
    ) -> None:
        """Test when previous search exists."""
        from tasks.keywords import get_pagination_state

        mock_session = MagicMock()
        query_result = mock_session.query.return_value
        query_result.filter.return_value.order_by.return_value.first.return_value = (
            sample_api_search_usage
        )
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        cursor, page = get_pagination_state("researcher", Platform.TWITTER)

        assert cursor == "cursor_abc123"
        assert page == 6  # previous page + 1
