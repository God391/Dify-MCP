#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - only needed for YAML inputs
    yaml = None

try:
    from jinja2 import Environment, TemplateSyntaxError, meta
except ImportError:  # pragma: no cover - lightweight checks still run
    Environment = None
    TemplateSyntaxError = Exception
    meta = None


DIFY_REFERENCE = re.compile(r"\{\{#[^{}#]+#\}\}")
PORTABLE_TWO_DECIMAL_PARAMS = {"temperature", "top_p"}


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    node_id: str
    title: str
    message: str


def unique_pairs(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate mapping key: {key!r}")
        result[key] = value
    return result


if yaml is not None:
    class UniqueSafeLoader(yaml.SafeLoader):
        def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
            # Reject explicit duplicates, while preserving valid YAML merge overrides.
            unique_pairs([
                (self.construct_object(key, deep=deep), None)
                for key, _ in node.value
                if key.tag != "tag:yaml.org,2002:merge"
            ])
            self.flatten_mapping(node)
            return super().construct_mapping(node, deep=deep)


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        value = json.loads(text, object_pairs_hook=unique_pairs)
    else:
        if yaml is None:
            raise RuntimeError("YAML input requires PyYAML; run with `uv run --with pyyaml ...`")
        value = yaml.load(text, Loader=UniqueSafeLoader)
    if not isinstance(value, dict):
        raise ValueError("workflow document must be an object")
    return value


def graph_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("workflow document must be an object")
    if "workflow" in document:
        workflow = document["workflow"]
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        graph = workflow.get("graph")
    else:
        graph = document.get("graph", document)
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("cannot find graph nodes")
    if not nodes:
        raise ValueError("graph nodes must not be empty")
    if "edges" in graph and (not isinstance(graph["edges"], list) or
                             any(not isinstance(edge, dict) for edge in graph["edges"])):
        raise ValueError("graph edges must be an array of objects")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
            raise ValueError(f"node[{index}] and its data must be objects")
        if not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError(f"node[{index}].id must be a nonempty string")
    return nodes


def decimal_places(value: Any) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return 0
        exponent = number.normalize().as_tuple().exponent
    except (InvalidOperation, ValueError):
        return 0
    return max(0, -exponent)


def variable_names(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {
        item["variable"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("variable"), str) and item["variable"]
    }


def check_jinja(
    template: str,
    declared: set[str],
    node_id: str,
    title: str,
    prefix: str,
    require_jinja2: bool,
) -> list[Issue]:
    issues: list[Issue] = []
    if Environment is None:
        issues.append(Issue("error" if require_jinja2 else "warning", "JINJA2_UNAVAILABLE", node_id, title, "模板语法及别名检查未完成：需要 Jinja2"))
        return issues

    environment = Environment()
    try:
        parsed = environment.parse(template)
        undeclared = set(meta.find_undeclared_variables(parsed)) - set(environment.globals) - declared
    except TemplateSyntaxError as exc:
        if "{{#" in template:
            issues.append(Issue("error", f"{prefix}_DIFY_REFERENCE", node_id, title, "Jinja2 解析失败且含 Dify 引用 {{#...#}}"))
        issues.append(Issue("error", f"{prefix}_SYNTAX", node_id, title, f"Jinja2 语法错误: {exc}"))
        return issues
    if undeclared:
        issues.append(Issue("error", f"{prefix}_UNDECLARED_ALIAS", node_id, title, f"未声明的 Jinja2 别名: {sorted(undeclared)}"))
    return issues


def active_prompt_items(prompt_template: Any) -> list[dict[str, Any]]:
    if isinstance(prompt_template, list):
        return [item for item in prompt_template if isinstance(item, dict)]
    if isinstance(prompt_template, dict):
        return [prompt_template]
    return []


def completion_param_blocks(value: Any, path: str = "data") -> list[tuple[str, dict[str, Any]]]:
    blocks: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        params = value.get("completion_params")
        if isinstance(params, dict):
            blocks.append((f"{path}.completion_params", params))
        for key, child in value.items():
            if key != "completion_params":
                blocks.extend(completion_param_blocks(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            blocks.extend(completion_param_blocks(child, f"{path}[{index}]"))
    return blocks


def scan_document(
    document: dict[str, Any],
    *,
    require_jinja2: bool = False,
    allow_high_precision_model_params: bool = False,
) -> list[Issue]:
    issues: list[Issue] = []
    seen_ids: set[str] = set()
    for node in graph_nodes(document):
        node_id = str(node.get("id", "<missing>"))
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        title = str(data.get("title", node_id))
        node_type = data.get("type")
        if node_id in seen_ids:
            issues.append(Issue("error", "DUPLICATE_NODE_ID", node_id, title, "节点 ID 重复"))
        seen_ids.add(node_id)
        expected_wrapper = {"iteration-start": "custom-iteration-start", "loop-start": "custom-loop-start"}.get(node_type)
        if expected_wrapper and node.get("type") != expected_wrapper:
            issues.append(Issue("error", "CONTAINER_START_WRAPPER", node_id, title, f"外层 type 必须是 {expected_wrapper}"))

        if node_type == "start":
            variables = data.get("variables")
            if isinstance(variables, list):
                for variable in variables:
                    if not isinstance(variable, dict) or variable.get("type") not in {"json", "json_object"}:
                        continue
                    if "default" in variable and not isinstance(variable.get("default"), str):
                        variable_name = variable.get("variable", "<unnamed>")
                        issues.append(
                            Issue(
                                "error",
                                "START_JSON_DEFAULT_TYPE",
                                node_id,
                                title,
                                f"Start 变量 {variable_name!r} 的 {variable.get('type')} 默认值必须是 JSON 字符串，不能是对象",
                            )
                        )

        if node_type == "template-transform":
            template = data.get("template", "")
            if not isinstance(template, str):
                issues.append(Issue("error", "TT_TEMPLATE_TYPE", node_id, title, "template 必须是字符串"))
            else:
                issues.extend(check_jinja(template, variable_names(data.get("variables")), node_id, title, "TT", require_jinja2))

        for path, params in completion_param_blocks(data):
            for name, value in params.items():
                if isinstance(value, float) and not math.isfinite(value):
                    issues.append(Issue("error", "MODEL_PARAM_NONFINITE", node_id, title, f"{path}.{name} 必须是有限数值"))
                elif (not allow_high_precision_model_params and name in PORTABLE_TWO_DECIMAL_PARAMS
                      and isinstance(value, (int, float)) and not isinstance(value, bool) and decimal_places(value) > 2):
                    issues.append(Issue("warning", "MODEL_PARAM_PRECISION", node_id, title, f"{path}.{name}={value} 超过保守精度；需核对 provider schema，不自动舍入"))

        if node_type != "llm":
            continue

        prompt_config = data.get("prompt_config") if isinstance(data.get("prompt_config"), dict) else {}
        jinja_variables = variable_names(prompt_config.get("jinja2_variables"))
        raw_prompts = data.get("prompt_template")
        if not isinstance(raw_prompts, (dict, list)) or (isinstance(raw_prompts, list) and
                (not raw_prompts or any(not isinstance(item, dict) for item in raw_prompts))):
            issues.append(Issue("error", "LLM_PROMPT_TYPE", node_id, title, "prompt_template 必须是对象或非空对象数组"))
            continue
        for index, prompt in enumerate(active_prompt_items(raw_prompts)):
            edition = prompt.get("edition_type") or "basic"
            if edition not in {"basic", "jinja2"}:
                issues.append(Issue("error", "LLM_PROMPT_EDITION", node_id, title, f"prompt[{index}] 未知 edition_type"))
                continue
            if edition == "jinja2":
                template = prompt.get("jinja2_text", "")
                if not isinstance(template, str):
                    issues.append(Issue("error", "LLM_JINJA_TEMPLATE_TYPE", node_id, title, f"prompt[{index}].jinja2_text 必须是字符串"))
                else:
                    issues.extend(check_jinja(template, jinja_variables, node_id, title, "LLM_JINJA", require_jinja2))
                continue

            text = prompt.get("text", "")
            if not isinstance(text, str):
                issues.append(Issue("error", "LLM_BASIC_TEXT_TYPE", node_id, title, f"prompt[{index}].text 必须是字符串"))
                continue
            stripped = DIFY_REFERENCE.sub("", text)
            if "{%" in stripped or "{{" in stripped:
                issues.append(Issue("warning", "LLM_BASIC_JINJA_SYNTAX", node_id, title, f"prompt[{index}].text 含 Jinja 样式文本；确认是字面示例还是未转换的动态模板"))

    unique: dict[tuple[str, str, str, str], Issue] = {}
    for issue in issues:
        unique[(issue.code, issue.node_id, issue.title, issue.message)] = issue
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Dify workflow runtime guardrails")
    parser.add_argument("path", type=Path)
    parser.add_argument("--require-jinja2", action="store_true")
    parser.add_argument("--allow-high-precision-model-params", action="store_true")
    args = parser.parse_args()
    try:
        document = load_document(args.path)
        issues = scan_document(
            document,
            require_jinja2=args.require_jinja2,
            allow_high_precision_model_params=args.allow_high_precision_model_params,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "load_error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    payload = {
        "ok": not any(issue.severity == "error" for issue in issues),
        "path": str(args.path),
        "coverage": {
            "scope": "static_runtime_guards_only",
            "jinja2_available": Environment is not None,
            "full_dsl_schema": False,
            "graph_edges_and_selectors": False,
            "code_execution": False,
            "provider_schema": False,
            "tenant_runtime": False,
        },
        "issues": [issue.__dict__ for issue in issues],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
