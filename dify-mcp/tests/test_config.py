from __future__ import annotations

import pytest

from dify_mcp.config import ConfigurationError, Settings, _normalize_console_url


def test_normalize_console_url() -> None:
    assert _normalize_console_url("https://example.com") == "https://example.com/console/api"
    assert _normalize_console_url("https://example.com/console/api/") == "https://example.com/console/api"


def test_reject_credentials_in_url() -> None:
    with pytest.raises(ConfigurationError):
        _normalize_console_url("https://user:pass@example.com")


def test_admin_headers_do_not_include_csrf() -> None:
    settings = Settings(
        console_api_url="https://example.com/console/api",
        admin_api_key="admin",
        workspace_id="workspace",
        access_token=None,
        csrf_token=None,
        csrf_cookie_name="csrf_token",
        tls_verify=True,
        timeout=30,
        preview_ttl_seconds=1800,
        generator_model_provider=None,
        generator_model_name=None,
        generator_model_mode="chat",
    )
    assert settings.request_headers() == {
        "Accept": "application/json",
        "Authorization": "Bearer admin",
        "X-WORKSPACE-ID": "workspace",
    }


def test_console_mutation_requires_csrf() -> None:
    settings = Settings(
        console_api_url="https://example.com/console/api",
        admin_api_key=None,
        workspace_id=None,
        access_token="jwt",
        csrf_token=None,
        csrf_cookie_name="csrf_token",
        tls_verify=True,
        timeout=30,
        preview_ttl_seconds=1800,
        generator_model_provider=None,
        generator_model_name=None,
        generator_model_mode="chat",
    )
    with pytest.raises(ConfigurationError):
        settings.require_mutation_auth()


def test_no_credentials_defaults_to_browser_bridge(monkeypatch) -> None:
    monkeypatch.setenv("DIFY_CONSOLE_API_URL", "https://cloud.dify.ai/console/api")
    monkeypatch.delenv("DIFY_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("DIFY_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("DIFY_CONSOLE_ACCESS_TOKEN", raising=False)
    settings = Settings.from_env()
    assert settings.auth_mode == "browser_bridge"
    assert settings.direct_auth_configured is False
    assert settings.request_headers() == {"Accept": "application/json"}
    with pytest.raises(ConfigurationError, match="browser bridge"):
        settings.require_direct_auth()
