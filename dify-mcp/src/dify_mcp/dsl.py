"""Compose and statically validate Dify 1.16.1 App DSL 0.7.0 documents."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import yaml

DSL_VERSION = "0.7.0"
GRAPH_MODES = {"workflow", "advanced-chat"}
TRIGGER_TYPES = {"trigger-schedule", "trigger-webhook", "trigger-plugin"}
TERMINALS = {"workflow": "end", "advanced-chat": "answer"}
SYSTEM_ROOTS = {"sys", "env", "conversation", "rag", "context", "$output"}
VARIABLE_REF_RE = re.compile(r"\{\{#([^#{}]+)#\}\}")
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "password",
    "client_secret",
    "credential_id",
    "credentials",
    "authorization",
    "access_token",
    "refresh_token",
}


def is_safe_no_auth_authorization(value: Any) -> bool:
    """Return whether an authorization value is Dify's credential-free HTTP sentinel."""

    return (
        isinstance(value, dict)
        and set(value).issubset({"type", "config"})
        and value.get("type") == "no-auth"
        and value.get("config") is None
    )


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    message: str
    location: str = ""

    def as_dict(self) -> dict[str, str]:
        value = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.location:
            value["location"] = self.location
        return value


@dataclass(slots=True)
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    def error(self, code: str, message: str, location: str = "") -> None:
        self.findings.append(Finding("error", code, message, location))

    def warn(self, code: str, message: str, location: str = "") -> None:
        self.findings.append(Finding("warning", code, message, location))

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "warning"]

    def as_dict(self) -> dict[str, Any]:
        schema_codes = {
            "yaml.parse",
            "document.type",
            "version",
            "kind",
            "app",
            "app.mode",
            "dependencies",
            "workflow",
            "graph",
            "graph.nodes",
            "graph.edges",
        }
        schema_valid = not any(item.code in schema_codes for item in self.errors)
        graph_valid = schema_valid and not self.errors
        return {
            "schema_valid": schema_valid,
            "graph_valid": graph_valid,
            "dify_import_compatible": graph_valid,
            "draft_synced": False,
            "publish_ready": "unknown",
            "runtime_ready": "unknown",
            "summary": {"errors": len(self.errors), "warnings": len(self.warnings)},
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
        }


def load_yaml(yaml_content: str, report: ValidationReport | None = None) -> Any:
    target = report or ValidationReport()
    try:
        return yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        target.error("yaml.parse", str(exc), "$")
        return None


def dump_yaml(document: dict[str, Any]) -> str:
    value = yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    # PyYAML currently quotes this value, but keep the version contract stable
    # across emitter releases.
    return re.sub(r"^version:\s+['\"]?0\.7\.0['\"]?\s*$", 'version: "0.7.0"', value, count=1, flags=re.MULTILINE)


def graph_fingerprint(graph: dict[str, Any]) -> str:
    canonical = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compose_document(
    *,
    graph: dict[str, Any],
    mode: str,
    app_name: str,
    description: str = "",
    icon: str = "🧩",
    icon_type: str = "emoji",
    icon_background: str = "#E4FBCC",
    use_icon_as_answer_icon: bool = False,
    features: dict[str, Any] | None = None,
    environment_variables: list[dict[str, Any]] | None = None,
    conversation_variables: list[dict[str, Any]] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if mode not in GRAPH_MODES:
        raise ValueError("mode must be workflow or advanced-chat")
    graph_copy = copy.deepcopy(graph)
    graph_copy.setdefault("viewport", {"x": 0, "y": 0, "zoom": 0.8})
    return {
        "version": DSL_VERSION,
        "kind": "app",
        "app": {
            "name": app_name.strip() or "Generated Workflow",
            "mode": mode,
            "icon": icon or "🧩",
            "icon_type": icon_type or "emoji",
            "icon_background": icon_background or "#E4FBCC",
            "description": description,
            "use_icon_as_answer_icon": bool(use_icon_as_answer_icon),
        },
        "dependencies": copy.deepcopy(dependencies or []),
        "workflow": {
            "conversation_variables": copy.deepcopy(conversation_variables or []),
            "environment_variables": copy.deepcopy(environment_variables or []),
            "features": copy.deepcopy(features or {}),
            "graph": graph_copy,
        },
    }


def make_portable_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return a shareable copy with secrets and tenant credential bindings removed."""

    result = copy.deepcopy(document)
    workflow = result.get("workflow")
    if isinstance(workflow, dict):
        variables = workflow.get("environment_variables")
        if isinstance(variables, list):
            for item in variables:
                if isinstance(item, dict) and item.get("value_type") == "secret":
                    item["value"] = ""

    def scrub(value: Any) -> None:
        if isinstance(value, dict):
            for key in list(value):
                if str(key).lower() in SENSITIVE_KEYS:
                    if str(key).lower() == "authorization" and is_safe_no_auth_authorization(value[key]):
                        continue
                    value.pop(key, None)
                else:
                    scrub(value[key])
        elif isinstance(value, list):
            for child in value:
                scrub(child)

    scrub(result)
    return result


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, str(key), child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _validate_sensitive_values(document: dict[str, Any], report: ValidationReport) -> None:
    for path, key, value in _walk(document):
        if key.lower() not in SENSITIVE_KEYS:
            continue
        if key.lower() == "authorization" and is_safe_no_auth_authorization(value):
            continue
        if value not in (None, "", {}, []):
            report.error(
                "secret.embedded",
                f"Sensitive field {key!r} must not be embedded in portable DSL.",
                path,
            )


def _validate_variable_references(
    document: dict[str, Any], node_ids: set[str], report: ValidationReport
) -> None:
    for path, key, value in _walk(document.get("workflow", {}).get("graph", {})):
        roots: list[str] = []
        if isinstance(value, str):
            roots.extend(match.group(1).split(".", 1)[0] for match in VARIABLE_REF_RE.finditer(value))
        if (
            key in {"variable_selector", "value_selector", "selector"}
            and isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], str)
        ):
            roots.append(value[0])
        for root in roots:
            if root not in node_ids and root not in SYSTEM_ROOTS:
                report.error("variable.unknown-source", f"Variable source {root!r} does not exist.", path)


def validate_document(document: Any) -> ValidationReport:
    report = ValidationReport()
    if not isinstance(document, dict):
        report.error("document.type", "Top-level YAML must be a mapping.", "$")
        return report
    if document.get("version") != DSL_VERSION:
        report.error("version", 'version must be the quoted string "0.7.0".', "$.version")
    if document.get("kind") != "app":
        report.error("kind", "kind must be 'app'.", "$.kind")
    app = document.get("app")
    if not isinstance(app, dict):
        report.error("app", "app must be a mapping.", "$.app")
        return report
    mode = app.get("mode")
    if mode not in GRAPH_MODES:
        report.error("app.mode", "Only workflow and advanced-chat are supported.", "$.app.mode")
    if not isinstance(document.get("dependencies"), list):
        report.error("dependencies", "dependencies must be a list.", "$.dependencies")
    workflow = document.get("workflow")
    if not isinstance(workflow, dict):
        report.error("workflow", "workflow must be a mapping.", "$.workflow")
        return report
    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        report.error("graph", "workflow.graph must be a mapping.", "$.workflow.graph")
        return report
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        report.error("graph.nodes", "workflow.graph.nodes must be a list.", "$.workflow.graph.nodes")
        return report
    if not isinstance(edges, list):
        report.error("graph.edges", "workflow.graph.edges must be a list.", "$.workflow.graph.edges")
        return report

    node_by_id: dict[str, dict[str, Any]] = {}
    types: dict[str, str] = {}
    for index, raw_node in enumerate(nodes):
        location = f"$.workflow.graph.nodes[{index}]"
        if not isinstance(raw_node, dict):
            report.error("node.type", "Node must be a mapping.", location)
            continue
        node_id = raw_node.get("id")
        if not isinstance(node_id, str) or not node_id:
            report.error("node.id", "Node id must be a non-empty string.", f"{location}.id")
            continue
        if node_id in node_by_id:
            report.error("node.duplicate", f"Duplicate node id {node_id!r}.", f"{location}.id")
            continue
        node_by_id[node_id] = raw_node
        data = raw_node.get("data")
        node_type = data.get("type") if isinstance(data, dict) else None
        if not isinstance(node_type, str) or not node_type:
            report.error("node.runtime-type", "Node data.type is required.", f"{location}.data.type")
            continue
        types[node_id] = node_type
        if node_type == "llm":
            context = data.get("context")
            if not isinstance(context, dict):
                report.error("llm.context", "LLM nodes must contain context configuration.", f"{location}.data.context")

    outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    seen_edge_ids: set[str] = set()
    for index, raw_edge in enumerate(edges):
        location = f"$.workflow.graph.edges[{index}]"
        if not isinstance(raw_edge, dict):
            report.error("edge.type", "Edge must be a mapping.", location)
            continue
        edge_id = raw_edge.get("id")
        if not isinstance(edge_id, str) or not edge_id:
            report.error("edge.id", "Edge id must be a non-empty string.", f"{location}.id")
        elif edge_id in seen_edge_ids:
            report.error("edge.duplicate", f"Duplicate edge id {edge_id!r}.", f"{location}.id")
        else:
            seen_edge_ids.add(edge_id)
        source, target = raw_edge.get("source"), raw_edge.get("target")
        if source not in node_by_id:
            report.error("edge.source", f"Unknown edge source {source!r}.", f"{location}.source")
            continue
        if target not in node_by_id:
            report.error("edge.target", f"Unknown edge target {target!r}.", f"{location}.target")
            continue
        handle = raw_edge.get("sourceHandle")
        outgoing[source].append((target, str(handle) if handle is not None else ""))
        incoming[target] += 1

    start_ids = [node_id for node_id, node_type in types.items() if node_type == "start"]
    trigger_ids = [node_id for node_id, node_type in types.items() if node_type in TRIGGER_TYPES]
    if start_ids and trigger_ids:
        report.error("entry.mixed", "Start and Trigger nodes cannot coexist.", "$.workflow.graph.nodes")
    if not start_ids and not trigger_ids:
        report.error("entry.missing", "A Start node or at least one Trigger is required.", "$.workflow.graph.nodes")
    if len(start_ids) > 1:
        report.error("entry.multiple-start", "Only one Start node is allowed.", "$.workflow.graph.nodes")

    terminal_type = TERMINALS.get(str(mode))
    terminal_ids = [node_id for node_id, node_type in types.items() if node_type == terminal_type]
    if not terminal_ids:
        report.error("terminal.missing", f"Mode {mode!r} requires a {terminal_type!r} node.", "$.workflow.graph.nodes")

    roots = start_ids or trigger_ids
    reachable: set[str] = set(roots)
    queue: deque[str] = deque(roots)
    while queue:
        source = queue.popleft()
        for target, _ in outgoing.get(source, []):
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    if terminal_ids and not any(node_id in reachable for node_id in terminal_ids):
        report.error("terminal.unreachable", "No required terminal is reachable from the entry.", "$.workflow.graph")
    for node_id, node in node_by_id.items():
        if node_id in reachable or node.get("parentId"):
            continue
        report.warn("node.unreachable", f"Node {node_id!r} is not reachable from the entry.", "$.workflow.graph")

    for node_id, node_type in types.items():
        if node_type == "if-else":
            data = node_by_id[node_id].get("data", {})
            case_ids = {
                str(item.get("id"))
                for item in data.get("cases", [])
                if isinstance(item, dict) and item.get("id") is not None
            }
            allowed = case_ids | {"false"}
            for _, handle in outgoing.get(node_id, []):
                if handle not in allowed:
                    report.error("branch.handle", f"Invalid if-else sourceHandle {handle!r}.", "$.workflow.graph.edges")

    _validate_variable_references(document, set(node_by_id), report)
    _validate_sensitive_values(document, report)

    dependency_sensitive_types = {"llm", "tool", "knowledge-retrieval", "question-classifier", "parameter-extractor", "agent"}
    if not document.get("dependencies") and any(item in dependency_sensitive_types for item in types.values()):
        report.warn(
            "dependencies.unresolved",
            "The graph contains tenant-bound model/tool nodes but the portable dependency list is empty; verify providers in the target workspace.",
            "$.dependencies",
        )
    return report


def validate_yaml(yaml_content: str) -> tuple[Any, ValidationReport]:
    report = ValidationReport()
    document = load_yaml(yaml_content, report)
    if report.errors:
        return document, report
    return document, validate_document(document)


def graph_summary(document: dict[str, Any]) -> dict[str, Any]:
    app = document.get("app", {})
    graph = document.get("workflow", {}).get("graph", {})
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    counts: dict[str, int] = defaultdict(int)
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("data"), dict):
            counts[str(node["data"].get("type", "unknown"))] += 1
    return {
        "name": app.get("name"),
        "mode": app.get("mode"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_types": dict(sorted(counts.items())),
        "dependency_count": len(document.get("dependencies", [])),
        "graph_sha256": graph_fingerprint(graph) if isinstance(graph, dict) else None,
    }
