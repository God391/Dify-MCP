from __future__ import annotations

import importlib.util
import sys
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("validate_runtime_guards.py")
SPEC = importlib.util.spec_from_file_location("validate_runtime_guards", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def document(nodes: list[dict]) -> dict:
    return {"workflow": {"graph": {"nodes": nodes, "edges": []}}}


class RuntimeGuardTests(unittest.TestCase):
    def test_malformed_graph_is_not_silently_accepted(self) -> None:
        for value in ({"workflow": None}, {"workflow": {}, "nodes": []}, document([]),
                      document([None]), document([{"id": "x", "data": None}]),
                      document([{"id": 1, "data": {}}])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.scan_document(value)

    def test_supported_input_envelopes(self) -> None:
        graph = {"nodes": [{"id": "s", "data": {"type": "start"}}], "edges": []}
        for value in (graph, {"graph": graph}, {"workflow": {"graph": graph}}):
            self.assertEqual(MODULE.scan_document(value), [])

    def test_duplicate_node_id(self) -> None:
        node = {"id": "same", "data": {"type": "start"}}
        self.assertIn("DUPLICATE_NODE_ID", {i.code for i in MODULE.scan_document(document([node, node]))})

    def test_container_wrappers(self) -> None:
        for kind in ("iteration-start", "loop-start"):
            node = {"id": "inner", "type": "custom", "data": {"type": kind}}
            self.assertIn("CONTAINER_START_WRAPPER", {i.code for i in MODULE.scan_document(document([node]))})
            node["type"] = f"custom-{kind}"
            self.assertEqual(MODULE.scan_document(document([node])), [])

    def test_jinja_ast_accepts_locals_raw_and_comments(self) -> None:
        templates = [
            "{% for key, value in entries %}{{ key }}:{{ value }}{% endfor %}",
            "{% macro greet(name) %}{{ name }}{% endmacro %}{{ greet('x') }}",
            "{% raw %}{{ literal }} {{#not.a.reference#}}{% endraw %}",
            "{# {{ ignored }} {{#ignored.output#}} #}",
            "{{ range(3) | list }}",
        ]
        for template in templates:
            with self.subTest(template=template):
                self.assertEqual(MODULE.check_jinja(template, {"entries"}, "t", "t", "TT", True), [])

    def test_jinja_ast_detects_missing_nested_alias(self) -> None:
        issues = MODULE.check_jinja("{{ missing.field | default('') }}", set(), "t", "t", "TT", True)
        self.assertIn("TT_UNDECLARED_ALIAS", {i.code for i in issues})

    def test_missing_jinja_dependency_is_explicit(self) -> None:
        with patch.object(MODULE, "Environment", None):
            for strict, severity in ((False, "warning"), (True, "error")):
                issues = MODULE.check_jinja("{{ value }}", {"value"}, "t", "t", "TT", strict)
                self.assertEqual([(i.code, i.severity) for i in issues], [("JINJA2_UNAVAILABLE", severity)])

    def test_inactive_prompt_field_is_ignored(self) -> None:
        value = document([{"id": "llm", "data": {"type": "llm", "prompt_template": [
            {"edition_type": "basic", "text": "hello", "jinja2_text": "{{ missing }}"}
        ]}}])
        self.assertEqual(MODULE.scan_document(value), [])

    def test_literal_basic_template_requires_review_not_auto_rewrite(self) -> None:
        value = document([{"id": "llm", "data": {"type": "llm", "prompt_template": [
            {"text": "Explain this example: {{ user }}"}
        ]}}])
        issues = MODULE.scan_document(value)
        self.assertEqual([(i.code, i.severity) for i in issues], [("LLM_BASIC_JINJA_SYNTAX", "warning")])

    def test_invalid_prompt_shape_and_mode(self) -> None:
        for prompts, code in (([None], "LLM_PROMPT_TYPE"), ([], "LLM_PROMPT_TYPE"),
                              ([{"text": {}}], "LLM_BASIC_TEXT_TYPE"),
                              ([{"edition_type": "unknown"}], "LLM_PROMPT_EDITION")):
            value = document([{"id": "llm", "data": {"type": "llm", "prompt_template": prompts}}])
            self.assertIn(code, {i.code for i in MODULE.scan_document(value)})

    def test_precision_is_warning_and_override_does_not_allow_nonfinite(self) -> None:
        value = document([{"id": "m", "data": {"type": "question-classifier", "model": {
            "completion_params": {"top_p": 0.001, "temperature": float("nan")}
        }}}])
        issues = MODULE.scan_document(value)
        self.assertEqual({(i.code, i.severity) for i in issues},
                         {("MODEL_PARAM_PRECISION", "warning"), ("MODEL_PARAM_NONFINITE", "error")})
        self.assertEqual([i.code for i in MODULE.scan_document(value, allow_high_precision_model_params=True)],
                         ["MODEL_PARAM_NONFINITE"])

    def test_duplicate_mapping_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for suffix, content in (("json", '{"nodes": [], "nodes": []}'),
                                    ("yml", "nodes: []\nnodes: []\n")):
                path = Path(directory) / f"duplicate.{suffix}"
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate mapping key"):
                    MODULE.load_document(path)

    def test_yaml_merge_override_and_bom_remain_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "合并.yml"
            path.write_text("base: &base {value: 1}\nresult: {<<: *base, value: 2}\n", encoding="utf-8-sig")
            self.assertEqual(MODULE.load_document(path)["result"]["value"], 2)

    def test_cli_exit_codes_and_coverage(self) -> None:
        cases = [(document([{"id": "s", "data": {"type": "start"}}]), 0),
                 (document([{"id": "i", "type": "custom", "data": {"type": "iteration-start"}}]), 1),
                 ({"workflow": None}, 2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "工作流.json"
            for value, expected in cases:
                path.write_text(json.dumps(value), encoding="utf-8")
                result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(MODULE_PATH), str(path)],
                                        capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, expected, result.stderr)
                payload = json.loads(result.stdout)
                if expected != 2:
                    self.assertFalse(payload["coverage"]["tenant_runtime"])
                else:
                    self.assertIn("load_error", payload)

    def test_accepts_string_default_for_start_json_object(self) -> None:
        value = document([
            {
                "id": "start",
                "data": {
                    "type": "start",
                    "title": "开始",
                    "variables": [{"variable": "review_content", "type": "json_object", "default": "{}"}],
                },
            }
        ])
        self.assertEqual(MODULE.scan_document(value), [])

    def test_rejects_object_default_for_start_json_object(self) -> None:
        value = document([
            {
                "id": "start",
                "data": {
                    "type": "start",
                    "title": "开始",
                    "variables": [{"variable": "review_content", "type": "json_object", "default": {}}],
                },
            }
        ])
        issues = MODULE.scan_document(value)
        self.assertIn("START_JSON_DEFAULT_TYPE", {issue.code for issue in issues})

    def test_valid_template_and_basic_prompt(self) -> None:
        value = document([
            {
                "id": "template",
                "data": {
                    "type": "template-transform",
                    "title": "模板",
                    "template": "{{ result_json }}",
                    "variables": [{"variable": "result_json", "value_selector": ["prepare", "result_json"]}],
                },
            },
            {
                "id": "llm",
                "data": {
                    "type": "llm",
                    "title": "模型",
                    "model": {"completion_params": {"temperature": 0.01, "top_p": 0.1}},
                    "prompt_template": [{"role": "user", "edition_type": "basic", "text": "{{#start.query#}}"}],
                },
            },
        ])
        self.assertEqual(MODULE.scan_document(value), [])

    def test_catches_observed_runtime_failures(self) -> None:
        value = document([
            {
                "id": "template",
                "data": {
                    "type": "template-transform",
                    "title": "错误模板",
                    "template": "{{#prepare.result#}}",
                    "variables": [{"variable": "result", "value_selector": ["prepare", "result"]}],
                },
            },
            {
                "id": "llm",
                "data": {
                    "type": "llm",
                    "title": "错误模型",
                    "model": {"completion_params": {"temperature": 0.001}},
                    "prompt_template": [{"role": "user", "edition_type": "basic", "text": "{% if value %}x{% endif %}"}],
                },
            },
        ])
        codes = {issue.code for issue in MODULE.scan_document(value)}
        self.assertTrue({"TT_DIFY_REFERENCE", "MODEL_PARAM_PRECISION", "LLM_BASIC_JINJA_SYNTAX"} <= codes)

    def test_jinja_prompt_requires_declared_alias(self) -> None:
        value = document([
            {
                "id": "llm",
                "data": {
                    "type": "llm",
                    "title": "Jinja 模型",
                    "model": {"completion_params": {}},
                    "prompt_config": {"jinja2_variables": []},
                    "prompt_template": [{"role": "user", "edition_type": "jinja2", "jinja2_text": "{{ query }}"}],
                },
            }
        ])
        self.assertIn("LLM_JINJA_UNDECLARED_ALIAS", {issue.code for issue in MODULE.scan_document(value)})

    def test_model_precision_guard_applies_beyond_llm_nodes(self) -> None:
        value = document([
            {
                "id": "classifier",
                "data": {
                    "type": "question-classifier",
                    "title": "分类器",
                    "model": {"completion_params": {"top_p": 0.001}},
                },
            }
        ])
        self.assertIn("MODEL_PARAM_PRECISION", {issue.code for issue in MODULE.scan_document(value)})


if __name__ == "__main__":
    unittest.main()
