#!/usr/bin/env python3
"""Unit tests for projects_integration module."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from core.desktop.devtools.interface.projects_integration import (
    validate_pat_token_http,
    _get_sync_service,
    _projects_status_payload,
    _invalidate_projects_status_cache,
)


class TestValidatePatTokenHttp:
    """Tests for validate_pat_token_http function."""

    def test_validate_pat_token_empty_token(self):
        """Test validation with empty token."""
        result, message = validate_pat_token_http("")
        assert result is False
        assert "PAT missing" in message

    def test_validate_pat_token_none_token(self):
        """Test validation with None token."""
        result, message = validate_pat_token_http(None)
        assert result is False
        assert "PAT missing" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_network_error(self, mock_post):
        """Test validation with network error."""
        import requests
        mock_post.side_effect = requests.RequestException("Connection timeout")
        result, message = validate_pat_token_http("test_token")
        assert result is False
        assert "Network unavailable" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_http_error(self, mock_post):
        """Test validation with HTTP error response."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_post.return_value = mock_response
        result, message = validate_pat_token_http("invalid_token")
        assert result is False
        assert "GitHub replied 401" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_graphql_error(self, mock_post):
        """Test validation with GraphQL error."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": [{"message": "Bad credentials"}]}
        mock_post.return_value = mock_response
        result, message = validate_pat_token_http("bad_token")
        assert result is False
        assert "Bad credentials" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_missing_viewer(self, mock_post):
        """Test validation with missing viewer in response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {}}
        mock_post.return_value = mock_response
        result, message = validate_pat_token_http("token")
        assert result is False
        assert "Response missing viewer" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_success(self, mock_post):
        """Test successful token validation."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"viewer": {"login": "testuser"}}}
        mock_post.return_value = mock_response
        result, message = validate_pat_token_http("valid_token")
        assert result is True
        assert "PAT valid" in message
        assert "testuser" in message

    @patch("core.desktop.devtools.interface.projects_integration.requests.post")
    def test_validate_pat_token_timeout(self, mock_post):
        """Test validation with custom timeout."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"viewer": {"login": "testuser"}}}
        mock_post.return_value = mock_response
        result, message = validate_pat_token_http("token", timeout=5.0)
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 5.0


class TestGetSyncService:
    """Tests for _get_sync_service function."""

    @patch("core.desktop.devtools.interface.projects_integration.get_projects_sync")
    @patch("core.desktop.devtools.interface.projects_integration.ProjectsSyncService")
    def test_get_sync_service(self, mock_service_class, mock_get_sync):
        """Test getting sync service."""
        mock_sync = Mock()
        mock_get_sync.return_value = mock_sync
        mock_service = Mock()
        mock_service_class.return_value = mock_service
        result = _get_sync_service()
        assert result == mock_service
        mock_service_class.assert_called_once_with(mock_sync)


class TestProjectsStatusPayload:
    """Tests for _projects_status_payload function."""

    @patch("core.desktop.devtools.interface.projects_integration._get_sync_service")
    @patch("core.desktop.devtools.interface.projects_integration.projects_status_cache")
    def test_projects_status_payload(self, mock_cache, mock_get_service):
        """Test getting projects status payload."""
        mock_service = Mock()
        mock_get_service.return_value = mock_service
        mock_payload = {"status": "OK", "enabled": True}
        mock_cache.projects_status_payload.return_value = mock_payload
        result = _projects_status_payload()
        assert result == mock_payload
        mock_cache.projects_status_payload.assert_called_once_with(mock_get_service, force_refresh=False)

    @patch("core.desktop.devtools.interface.projects_integration._get_sync_service")
    @patch("core.desktop.devtools.interface.projects_integration.projects_status_cache")
    def test_projects_status_payload_force_refresh(self, mock_cache, mock_get_service):
        """Test getting projects status payload with force refresh."""
        mock_service = Mock()
        mock_get_service.return_value = mock_service
        mock_payload = {"status": "OK", "enabled": True}
        mock_cache.projects_status_payload.return_value = mock_payload
        result = _projects_status_payload(force_refresh=True)
        assert result == mock_payload
        mock_cache.projects_status_payload.assert_called_once_with(mock_get_service, force_refresh=True)


class TestInvalidateProjectsStatusCache:
    """Tests for _invalidate_projects_status_cache function."""

    @patch("core.desktop.devtools.interface.projects_integration.projects_status_cache")
    def test_invalidate_projects_status_cache(self, mock_cache):
        """Test invalidating projects status cache."""
        _invalidate_projects_status_cache()
        mock_cache.invalidate_cache.assert_called_once()
