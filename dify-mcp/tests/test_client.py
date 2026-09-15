from __future__ import annotations

import httpx
import pytest

from dify_mcp.client import DifyAPIError, DifyClient
from dify_mcp.config import Settings


def settings() -> Settings:
    return Settings(
        console_api_url="https://dify.example.com/console/api",
        admin_api_key="admin-key",
        workspace_id="workspace-id",
        access_token=None,
        csrf_token=None,
        csrf_cookie_name="csrf_token",
        tls_verify=True,
        timeout=30,
        preview_ttl_seconds=1800,
        generator_model_provider="provider",
        generator_model_name="model",
        generator_model_mode="chat",
    )


def test_generate_graph_contract_and_auth_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/console/api/workflow-generate"
        assert request.headers["authorization"] == "Bearer admin-key"
        assert request.headers["x-workspace-id"] == "workspace-id"
        body = __import__("json").loads(request.content)
        assert body["mode"] == "workflow"
        assert body["model_config"]["name"] == "model"
        return httpx.Response(200, json={"graph": {"nodes": [], "edges": [], "viewport": {}}})

    client = DifyClient(settings(), transport=httpx.MockTransport(handler))
    response = client.generate_graph(
        instruction="Build a workflow",
        mode="workflow",
        model_config={"provider": "provider", "name": "model", "mode": "chat", "completion_params": {}},
    )
    assert response["_http_status"] == 200


def test_api_error_is_structured() -> None:
    client = DifyClient(
        settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(409, json={"message": "hash conflict"})),
    )
    with pytest.raises(DifyAPIError) as exc_info:
        client.get_draft("app")
    assert exc_info.value.status_code == 409
    assert "hash conflict" in str(exc_info.value)

