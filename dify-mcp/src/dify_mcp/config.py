"""Environment-only configuration for the Dify console client.

Secrets are intentionally not accepted as MCP tool arguments. This prevents
them from being copied into model-visible tool transcripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required connection settings are missing or unsafe."""


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _normalize_console_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ConfigurationError("DIFY_CONSOLE_API_URL is required")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("DIFY_CONSOLE_API_URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ConfigurationError("Credentials must not be embedded in DIFY_CONSOLE_API_URL")
    if not value.endswith("/console/api"):
        value += "/console/api"
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    console_api_url: str
    admin_api_key: str | None
    workspace_id: str | None
    access_token: str | None
    csrf_token: str | None
    csrf_cookie_name: str
    tls_verify: bool
    timeout: float
    preview_ttl_seconds: int
    generator_model_provider: str | None
    generator_model_name: str | None
    generator_model_mode: str

    @classmethod
    def from_env(cls) -> Settings:
        timeout = float(os.getenv("DIFY_HTTP_TIMEOUT", "120"))
        ttl = int(os.getenv("DIFY_PREVIEW_TTL_SECONDS", "1800"))
        if timeout <= 0:
            raise ConfigurationError("DIFY_HTTP_TIMEOUT must be positive")
        if ttl < 60:
            raise ConfigurationError("DIFY_PREVIEW_TTL_SECONDS must be at least 60")
        settings = cls(
            console_api_url=_normalize_console_url(os.getenv("DIFY_CONSOLE_API_URL", "")),
            admin_api_key=os.getenv("DIFY_ADMIN_API_KEY") or None,
            workspace_id=os.getenv("DIFY_WORKSPACE_ID") or None,
            access_token=os.getenv("DIFY_CONSOLE_ACCESS_TOKEN") or None,
            csrf_token=os.getenv("DIFY_CSRF_TOKEN") or None,
            csrf_cookie_name=os.getenv("DIFY_CSRF_COOKIE_NAME", "csrf_token").strip() or "csrf_token",
            tls_verify=_bool_env("DIFY_TLS_VERIFY", True),
            timeout=timeout,
            preview_ttl_seconds=ttl,
            generator_model_provider=os.getenv("DIFY_GENERATOR_MODEL_PROVIDER") or None,
            generator_model_name=os.getenv("DIFY_GENERATOR_MODEL_NAME") or None,
            generator_model_mode=os.getenv("DIFY_GENERATOR_MODEL_MODE", "chat").strip() or "chat",
        )
        settings.validate_auth()
        return settings

    def validate_auth(self) -> None:
        if self.admin_api_key:
            if not self.workspace_id:
                raise ConfigurationError("DIFY_WORKSPACE_ID is required with DIFY_ADMIN_API_KEY")
        elif self.workspace_id:
            raise ConfigurationError("DIFY_ADMIN_API_KEY is required with DIFY_WORKSPACE_ID")

    @property
    def auth_mode(self) -> str:
        if self.admin_api_key:
            return "admin_api_key"
        if self.access_token:
            return "console_access_token"
        return "browser_bridge"

    @property
    def direct_auth_configured(self) -> bool:
        return bool(self.admin_api_key or self.access_token)

    def request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.admin_api_key:
            headers["Authorization"] = f"Bearer {self.admin_api_key}"
            headers["X-WORKSPACE-ID"] = self.workspace_id or ""
            return headers
        if not self.access_token:
            return headers
        headers["Authorization"] = f"Bearer {self.access_token}"
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
            headers["Cookie"] = f"{self.csrf_cookie_name}={self.csrf_token}"
        return headers

    def require_direct_auth(self) -> None:
        if not self.direct_auth_configured:
            raise ConfigurationError(
                "Direct Dify authentication is not configured. Use the browser bridge tools with "
                "a signed-in exact-origin page, or set DIFY_ADMIN_API_KEY + DIFY_WORKSPACE_ID / "
                "DIFY_CONSOLE_ACCESS_TOKEN."
            )

    def require_mutation_auth(self) -> None:
        self.require_direct_auth()
        if self.admin_api_key:
            return
        if not self.csrf_token:
            raise ConfigurationError(
                "Generator/import operations with a console access token require DIFY_CSRF_TOKEN"
            )

    def resolve_generator_model(
        self,
        provider: str | None,
        name: str | None,
        mode: str | None,
    ) -> dict[str, object]:
        resolved_provider = (provider or self.generator_model_provider or "").strip()
        resolved_name = (name or self.generator_model_name or "").strip()
        resolved_mode = (mode or self.generator_model_mode or "chat").strip()
        if not resolved_provider or not resolved_name:
            raise ConfigurationError(
                "Generator model is required: pass model_provider/model_name or set "
                "DIFY_GENERATOR_MODEL_PROVIDER and DIFY_GENERATOR_MODEL_NAME"
            )
        return {
            "provider": resolved_provider,
            "name": resolved_name,
            "mode": resolved_mode,
            "completion_params": {},
        }
