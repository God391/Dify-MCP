from __future__ import annotations

import copy

import pytest

from dify_mcp.patching import WorkflowPatchError, apply_workflow_patch, workflow_fingerprint

from .test_dsl import simple_graph


def workflow_payload() -> dict:
    return {
        "graph": simple_graph(),
        "features": {"file_upload": {"enabled": False}, "opening_statement": ""},
        "environment_variables": [
            {"id": "env-public", "name": "REGION", "value_type": "string", "value": "cn"},
            {"id": "env-secret", "name": "TOKEN", "value_type": "secret", "value": "kept-secret"},
        ],
        "conversation_variables": [],
    }


def test_replace_by_stable_id_preserves_every_other_field() -> None:
    base = workflow_payload()
    untouched = copy.deepcopy(base)
    result = apply_workflow_patch(
        base,
        [{"op": "replace", "path": "/graph/nodes/@start/data/title", "value": "Input"}],
    )

    assert result.workflow["graph"]["nodes"][0]["data"]["title"] == "Input"
    untouched["graph"]["nodes"][0]["data"]["title"] = "Input"
    assert result.workflow == untouched
    assert base == workflow_payload()
    assert result.round_trip_verified is True
    assert result.changes[0].resolved_path == "/graph/nodes/0/data/title"


def test_add_and_remove_list_items_are_reversible() -> None:
    base = workflow_payload()
    result = apply_workflow_patch(
        base,
        [
            {
                "op": "add",
                "path": "/graph/nodes/-",
                "value": {
                    "id": "middle",
                    "type": "custom",
                    "position": {"x": 150, "y": 0},
                    "data": {"type": "template-transform", "title": "Middle"},
                },
            },
            {"op": "remove", "path": "/graph/nodes/@middle"},
        ],
    )
    assert result.workflow == base
    assert result.round_trip_verified is True
    assert len(result.changes) == 2


def test_add_http_node_with_no_auth_sentinel() -> None:
    result = apply_workflow_patch(
        workflow_payload(),
        [
            {
                "op": "add",
                "path": "/graph/nodes/-",
                "value": {
                    "id": "http",
                    "type": "custom",
                    "position": {"x": 150, "y": 0},
                    "data": {
                        "type": "http-request",
                        "title": "HTTP",
                        "authorization": {"type": "no-auth", "config": None},
                    },
                },
            }
        ],
    )

    assert result.workflow["graph"]["nodes"][-1]["data"]["authorization"] == {
        "type": "no-auth",
        "config": None,
    }


def test_test_guard_must_match() -> None:
    with pytest.raises(WorkflowPatchError, match="Test operation failed"):
        apply_workflow_patch(
            workflow_payload(),
            [{"op": "test", "path": "/graph/nodes/@start/data/title", "value": "Wrong"}],
        )


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "/graph", "value": {}},
        {"op": "replace", "path": "/graph/nodes/@start/data/credential_id", "value": "x"},
        {"op": "replace", "path": "/environment_variables/@env-secret/value", "value": "x"},
        {
            "op": "add",
            "path": "/environment_variables/-",
            "value": {"name": "SECRET", "value_type": "secret", "value": "x"},
        },
    ],
)
def test_unsafe_patch_targets_are_rejected(operation: dict) -> None:
    with pytest.raises(WorkflowPatchError):
        apply_workflow_patch(workflow_payload(), [operation])


def test_patch_fingerprint_is_stable_and_changes_only_after_mutation() -> None:
    base = workflow_payload()
    result = apply_workflow_patch(
        base,
        [{"op": "replace", "path": "/features/opening_statement", "value": "Hello"}],
    )
    assert workflow_fingerprint(base) == workflow_fingerprint(copy.deepcopy(base))
    assert workflow_fingerprint(result.workflow) != workflow_fingerprint(base)
