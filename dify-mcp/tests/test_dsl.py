from __future__ import annotations

from dify_mcp.dsl import (
    compose_document,
    dump_yaml,
    make_portable_document,
    validate_document,
    validate_yaml,
)


def simple_graph(terminal: str = "end") -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 0, "y": 0},
                "data": {"type": "start", "title": "Start", "variables": []},
            },
            {
                "id": "finish",
                "type": "custom",
                "position": {"x": 300, "y": 0},
                "data": {"type": terminal, "title": "Finish", "outputs": []},
            },
        ],
        "edges": [
            {
                "id": "start-finish",
                "type": "custom",
                "source": "start",
                "target": "finish",
                "data": {"sourceType": "start", "targetType": terminal},
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 0.8},
    }


def test_valid_workflow_and_quoted_version() -> None:
    document = compose_document(graph=simple_graph(), mode="workflow", app_name="Demo")
    report = validate_document(document)
    assert not report.errors
    dumped = dump_yaml(document)
    assert dumped.startswith('version: "0.7.0"\n')
    parsed, roundtrip = validate_yaml(dumped)
    assert parsed["app"]["name"] == "Demo"
    assert not roundtrip.errors


def test_chatflow_requires_answer() -> None:
    document = compose_document(graph=simple_graph(), mode="advanced-chat", app_name="Demo")
    report = validate_document(document)
    assert "terminal.missing" in {item.code for item in report.errors}


def test_unknown_edge_endpoint_is_rejected() -> None:
    graph = simple_graph()
    graph["edges"][0]["target"] = "missing"
    document = compose_document(graph=graph, mode="workflow", app_name="Demo")
    report = validate_document(document)
    assert "edge.target" in {item.code for item in report.errors}


def test_portable_copy_removes_secrets_without_mutating_source() -> None:
    document = compose_document(
        graph=simple_graph(),
        mode="workflow",
        app_name="Demo",
        environment_variables=[{"name": "TOKEN", "value_type": "secret", "value": "secret-value"}],
    )
    document["workflow"]["graph"]["nodes"][0]["data"]["credential_id"] = "private-id"
    portable = make_portable_document(document)
    assert portable["workflow"]["environment_variables"][0]["value"] == ""
    assert "credential_id" not in portable["workflow"]["graph"]["nodes"][0]["data"]
    assert document["workflow"]["environment_variables"][0]["value"] == "secret-value"


def test_no_auth_http_authorization_is_valid_and_portable() -> None:
    document = compose_document(graph=simple_graph(), mode="workflow", app_name="Demo")
    authorization = {"type": "no-auth", "config": None}
    document["workflow"]["graph"]["nodes"][0]["data"]["authorization"] = authorization

    report = validate_document(document)
    assert "secret.embedded" not in {item.code for item in report.errors}
    portable = make_portable_document(document)
    assert portable["workflow"]["graph"]["nodes"][0]["data"]["authorization"] == authorization


def test_credential_bearing_authorization_is_rejected() -> None:
    document = compose_document(graph=simple_graph(), mode="workflow", app_name="Demo")
    document["workflow"]["graph"]["nodes"][0]["data"]["authorization"] = {
        "type": "api-key",
        "config": {"type": "bearer", "api_key": "secret"},
    }

    report = validate_document(document)
    assert "secret.embedded" in {item.code for item in report.errors}
