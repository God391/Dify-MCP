from __future__ import annotations

from typing import Any

import pytest

from dify_mcp import server
from dify_mcp.artifacts import PreviewStore
from dify_mcp.config import Settings
from dify_mcp.patching import apply_workflow_patch

from .test_dsl import simple_graph


class FakeClient:
    def __init__(self) -> None:
        self.graph = simple_graph()
        self.features = {"file_upload": {"enabled": False}}
        self.environment_variables = [
            {"id": "env-1", "name": "TOKEN", "value_type": "secret", "value": "kept"}
        ]
        self.conversation_variables: list[dict[str, Any]] = []
        self.hash = "hash-1"
        self.synced = False
        self.mutate_readback = False

    def get_app(self, app_id: str) -> dict[str, Any]:
        return {"id": app_id, "name": "Existing", "mode": "workflow", "description": ""}

    def get_draft(self, app_id: str) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "hash": self.hash,
            "features": self.features,
            "environment_variables": self.environment_variables,
            "conversation_variables": self.conversation_variables,
        }

    def generate_graph(self, **_: Any) -> dict[str, Any]:
        return {"graph": simple_graph(), "mode": "workflow", "app_name": "Generated"}

    def import_dsl(self, yaml_content: str) -> dict[str, Any]:
        assert 'version: "0.7.0"' in yaml_content
        return {"app_id": "new-app", "status": "completed", "_http_status": 200}

    def check_dependencies(self, app_id: str) -> dict[str, Any]:
        return {"leaked_dependencies": [], "_http_status": 200}

    def sync_draft(self, app_id: str, **kwargs: Any) -> dict[str, Any]:
        self.graph = kwargs["graph"]
        self.features = kwargs["features"]
        self.environment_variables = kwargs["environment_variables"]
        self.conversation_variables = kwargs["conversation_variables"]
        if self.mutate_readback:
            self.features["server_normalized_unrequested"] = True
        self.hash = "hash-2"
        self.synced = True
        return {"result": "success", "hash": self.hash, "_http_status": 200}

    def close(self) -> None:
        pass


def runtime(fake: FakeClient) -> server.Runtime:
    settings = Settings(
        console_api_url="https://dify.example.com/console/api",
        admin_api_key="admin",
        workspace_id="workspace",
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
    return server.Runtime(settings=settings, client=fake, previews=PreviewStore(ttl_seconds=1800))  # type: ignore[arg-type]


def test_generate_then_create_uses_pinned_preview(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_generate_dsl("Create a simple flow")
    assert preview["write_performed"] is False
    assert preview["validation"]["graph_valid"] is True
    result = server.workflow_create_draft_apply(preview["preview_id"])
    assert result["app_id"] == "new-app"
    assert result["draft_synced"] is True


def test_refine_update_checks_hash_and_reads_back(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_generate_dsl("Refine the flow", app_id="app-1")
    assert preview["base_hash"] == "hash-1"
    result = server.workflow_update_draft_apply(preview["preview_id"], "app-1", "hash-1")
    assert result["status"] == "updated"
    assert result["draft_hash"] == "hash-2"
    assert fake.synced is True


def test_get_draft_exposes_stable_patch_inventory(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    result = server.workflow_get_draft("app-1")
    assert result["patch_inventory"]["nodes"][0] == {
        "id": "start",
        "type": "start",
        "title": "Start",
        "patch_path": "/graph/nodes/@start",
    }


def test_patch_preview_then_apply_preserves_unspecified_fields(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    before = fake.get_draft("app-1")
    preview = server.workflow_patch_draft_preview(
        "app-1",
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )

    assert preview["write_performed"] is False
    assert preview["invariants"]["unspecified_fields_unchanged"] is True
    assert preview["changes"][0]["resolved_path"] == "/graph/nodes/0/data/title"
    assert fake.synced is False

    result = server.workflow_patch_draft_apply(preview["preview_id"], "app-1", "hash-1")
    assert result["status"] == "patched"
    assert result["draft_synced"] is True
    assert result["unspecified_fields_unchanged"] is True
    assert fake.graph["nodes"][0]["data"]["title"] == "Input"
    assert fake.graph["nodes"][1:] == before["graph"]["nodes"][1:]
    assert fake.graph["edges"] == before["graph"]["edges"]
    assert fake.features == before["features"]
    assert fake.environment_variables == before["environment_variables"]


def test_patch_apply_rejects_hash_conflict_without_write(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_patch_draft_preview(
        "app-1",
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )
    fake.hash = "hash-other"
    result = server.workflow_patch_draft_apply(preview["preview_id"], "app-1", "hash-1")
    assert result["status"] == "conflict"
    assert result["write_performed"] is False
    assert fake.synced is False


def test_patch_apply_rejects_snapshot_drift_even_when_hash_is_stale(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_patch_draft_preview(
        "app-1",
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )
    fake.features["opening_statement"] = "another collaborator"
    result = server.workflow_patch_draft_apply(preview["preview_id"], "app-1", "hash-1")
    assert result["status"] == "conflict"
    assert result["write_performed"] is False
    assert fake.synced is False


def test_patch_apply_reports_unexpected_readback_change(monkeypatch) -> None:
    fake = FakeClient()
    fake.mutate_readback = True
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_patch_draft_preview(
        "app-1",
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )
    result = server.workflow_patch_draft_apply(preview["preview_id"], "app-1", "hash-1")
    assert result["status"] == "patched_readback_mismatch"
    assert result["write_performed"] is True
    assert result["draft_synced"] is False
    assert result["unspecified_fields_unchanged"] is False


def test_patch_preview_cannot_use_full_replace_apply(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    preview = server.workflow_patch_draft_preview(
        "app-1",
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )
    with pytest.raises(ValueError, match="workflow_patch_draft_apply"):
        server.workflow_update_draft_apply(preview["preview_id"], "app-1", "hash-1")


def test_browser_bridge_patch_prepare_and_verify_without_direct_write(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    protected_hash = "a" * 64
    raw_base_hash = "b" * 64
    raw_patched_hash = "c" * 64
    operations = [
        {"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}
    ]
    preview = server.workflow_browser_bridge_patch_preview(
        app_id="app-1",
        draft_hash="hash-1",
        mode="workflow",
        graph=fake.graph,
        features=fake.features,
        raw_projection_sha256=raw_base_hash,
        protected_sections_sha256=protected_hash,
        operations=operations,
    )
    assert preview["write_performed"] is False
    prepared = server.workflow_browser_bridge_patch_apply_prepare(
        preview_id=preview["preview_id"],
        app_id="app-1",
        expected_hash="hash-1",
        current_hash="hash-1",
        current_graph=fake.graph,
        current_features=fake.features,
        current_raw_projection_sha256=raw_base_hash,
        current_protected_sections_sha256=protected_hash,
    )
    assert prepared["status"] == "browser_bridge_apply_prepared"
    assert prepared["browser_bridge_protocol_version"] == "2"
    assert prepared["page_patch"]["operations"] == operations
    assert "graph" not in prepared["page_patch"]
    assert "features" not in prepared["page_patch"]
    assert fake.synced is False

    patched = apply_workflow_patch(
        {"graph": fake.graph, "features": fake.features}, operations
    ).workflow

    verified = server.workflow_browser_bridge_patch_verify(
        preview_id=preview["preview_id"],
        app_id="app-1",
        readback_graph=patched["graph"],
        readback_features=patched["features"],
        readback_protected_sections_sha256=protected_hash,
        expected_raw_patched_projection_sha256=raw_patched_hash,
        readback_raw_projection_sha256=raw_patched_hash,
        readback_hash="hash-2",
    )
    assert verified["status"] == "browser_bridge_verified"
    assert verified["unspecified_fields_unchanged"] is True
    assert verified["write_performed_by_mcp"] is False


def test_browser_bridge_prepare_rejects_hash_conflict(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    protected_hash = "b" * 64
    raw_base_hash = "c" * 64
    preview = server.workflow_browser_bridge_patch_preview(
        app_id="app-1",
        draft_hash="hash-1",
        mode="workflow",
        graph=fake.graph,
        features=fake.features,
        raw_projection_sha256=raw_base_hash,
        protected_sections_sha256=protected_hash,
        operations=[
            {"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}
        ],
    )
    prepared = server.workflow_browser_bridge_patch_apply_prepare(
        preview_id=preview["preview_id"],
        app_id="app-1",
        expected_hash="hash-1",
        current_hash="hash-other",
        current_graph=fake.graph,
        current_features=fake.features,
        current_raw_projection_sha256=raw_base_hash,
        current_protected_sections_sha256=protected_hash,
    )
    assert prepared["status"] == "conflict"
    assert prepared["write_performed"] is False
    assert fake.synced is False


def test_browser_bridge_prepare_rejects_raw_projection_conflict(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    protected_hash = "d" * 64
    preview = server.workflow_browser_bridge_patch_preview(
        app_id="app-1",
        draft_hash="hash-1",
        mode="workflow",
        graph=fake.graph,
        features=fake.features,
        raw_projection_sha256="e" * 64,
        protected_sections_sha256=protected_hash,
        operations=[
            {"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}
        ],
    )
    prepared = server.workflow_browser_bridge_patch_apply_prepare(
        preview_id=preview["preview_id"],
        app_id="app-1",
        expected_hash="hash-1",
        current_hash="hash-1",
        current_graph=fake.graph,
        current_features=fake.features,
        current_raw_projection_sha256="f" * 64,
        current_protected_sections_sha256=protected_hash,
    )
    assert prepared["status"] == "conflict"
    assert prepared["write_performed"] is False


def test_browser_bridge_verify_rejects_raw_readback_mismatch(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "_runtime_instance", runtime(fake))
    operations = [
        {"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}
    ]
    preview = server.workflow_browser_bridge_patch_preview(
        app_id="app-1",
        draft_hash="hash-1",
        mode="workflow",
        graph=fake.graph,
        features=fake.features,
        raw_projection_sha256="1" * 64,
        protected_sections_sha256="2" * 64,
        operations=operations,
    )
    patched = apply_workflow_patch(
        {"graph": fake.graph, "features": fake.features}, operations
    ).workflow
    verified = server.workflow_browser_bridge_patch_verify(
        preview_id=preview["preview_id"],
        app_id="app-1",
        readback_graph=patched["graph"],
        readback_features=patched["features"],
        readback_protected_sections_sha256="2" * 64,
        expected_raw_patched_projection_sha256="3" * 64,
        readback_raw_projection_sha256="4" * 64,
        readback_hash="hash-2",
    )
    assert verified["status"] == "browser_bridge_readback_mismatch"
    assert verified["raw_projection_match"] is False
    assert verified["unspecified_fields_unchanged"] is False
