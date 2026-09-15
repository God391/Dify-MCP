"""Small, version-pinned client for Dify 1.16.1 console APIs."""

from __future__ import annotations

from typing import Any, Self

import httpx

from .config import Settings


class DifyAPIError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int, detail: str):
        super().__init__(f"Dify API {method} {path} failed ({status_code}): {detail}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail


class DifyClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.console_api_url + "/",
            headers=settings.request_headers(),
            timeout=settings.timeout,
            verify=settings.tls_verify,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.settings.require_direct_auth()
        normalized = path.lstrip("/")
        response = self._client.request(method, normalized, **kwargs)
        try:
            payload: Any = response.json() if response.content else {}
        except ValueError:
            payload = {"message": response.text[:2000]}
        if response.status_code >= 400:
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or payload.get("code") or payload)
            else:
                detail = str(payload)
            raise DifyAPIError(method, "/" + normalized, response.status_code, detail[:2000])
        if not isinstance(payload, dict):
            raise DifyAPIError(method, "/" + normalized, response.status_code, "Expected a JSON object")
        payload["_http_status"] = response.status_code
        return payload

    def connection_check(self) -> dict[str, Any]:
        return self._request("GET", "apps", params={"page": 1, "limit": 1})

    def get_app(self, app_id: str) -> dict[str, Any]:
        return self._request("GET", f"apps/{app_id}")

    def get_draft(self, app_id: str) -> dict[str, Any]:
        return self._request("GET", f"apps/{app_id}/workflows/draft")

    def generate_graph(
        self,
        *,
        instruction: str,
        mode: str,
        model_config: dict[str, object],
        ideal_output: str = "",
        current_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.settings.require_mutation_auth()
        body: dict[str, Any] = {
            "mode": mode,
            "instruction": instruction,
            "ideal_output": ideal_output,
            "model_config": model_config,
        }
        if current_graph is not None:
            body["current_graph"] = current_graph
        return self._request("POST", "workflow-generate", json=body)

    def import_dsl(self, yaml_content: str) -> dict[str, Any]:
        self.settings.require_mutation_auth()
        return self._request(
            "POST",
            "apps/imports",
            json={"mode": "yaml-content", "yaml_content": yaml_content},
        )

    def sync_draft(
        self,
        app_id: str,
        *,
        graph: dict[str, Any],
        features: dict[str, Any],
        environment_variables: list[dict[str, Any]],
        conversation_variables: list[dict[str, Any]],
        expected_hash: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"apps/{app_id}/workflows/draft",
            json={
                "graph": graph,
                "features": features,
                "environment_variables": environment_variables,
                "conversation_variables": conversation_variables,
                "hash": expected_hash,
            },
        )

    def check_dependencies(self, app_id: str) -> dict[str, Any]:
        return self._request("GET", f"apps/imports/{app_id}/check-dependencies")
