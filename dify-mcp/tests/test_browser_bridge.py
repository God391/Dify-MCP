from __future__ import annotations

import pytest

from dify_mcp.browser_bridge import (
    BRIDGE_PROTOCOL_VERSION,
    bridge_projection,
    validate_bridge_operations,
    validate_protected_sections_sha256,
    validate_sha256,
)
from dify_mcp.patching import WorkflowPatchError


def test_bridge_rejects_sensitive_snapshot() -> None:
    with pytest.raises(WorkflowPatchError, match="sensitive field"):
        bridge_projection({"nodes": [], "edges": [], "credential_id": "tenant-value"}, {})


def test_bridge_operations_cannot_touch_protected_variables() -> None:
    with pytest.raises(WorkflowPatchError, match="limited to /graph and /features"):
        validate_bridge_operations(
            [{"op": "replace", "path": "/environment_variables/0/name", "value": "X"}]
        )


def test_protected_hash_must_be_sha256_hex() -> None:
    assert BRIDGE_PROTOCOL_VERSION == "2"
    assert validate_protected_sections_sha256("A" * 64) == "a" * 64
    assert validate_sha256("B" * 64, "raw_projection_sha256") == "b" * 64
    with pytest.raises(WorkflowPatchError, match="SHA-256"):
        validate_protected_sections_sha256("not-a-hash")
