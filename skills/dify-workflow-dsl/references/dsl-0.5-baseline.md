# Dify App DSL 0.5.0 兼容基线

## 适用范围

- 默认目标：Dify 1.11.x
- App DSL：`0.5.0`
- 目标：在较新的 Dify 中仍可导入，同时避免向只支持 0.5.0 的内网实例输出 0.7.0 专属字段。

官方源码核对入口以 Dify `1.11.1` 标签为准：

- `api/services/app_dsl_service.py`：`CURRENT_DSL_VERSION = "0.5.0"`
- `api/core/workflow/nodes/http_request/entities.py`
- `api/core/workflow/nodes/iteration/entities.py`
- `api/core/workflow/nodes/llm/entities.py`
- `api/core/workflow/nodes/code/entities.py`

## 顶层结构

```yaml
version: "0.5.0"
kind: app
app:
  name: "Example"
  mode: workflow
  icon: "🧩"
  icon_type: emoji
  icon_background: "#E4FBCC"
  description: ""
  use_icon_as_answer_icon: false
dependencies: []
workflow:
  conversation_variables: []
  environment_variables: []
  features: {}
  graph:
    nodes: []
    edges: []
    viewport: {x: 0, y: 0, zoom: 0.8}
```

## 已核对的节点差异

默认只检查以下已知节点：`start`、`end`、`http-request`、`code`、`if-else`、`llm`、`template-transform`、`variable-aggregator`、`iteration`、`iteration-start`。未知节点和插件节点不触发额外源码调查；保留其配置，并在交付中标记为未验证。

### HTTP Request

0.5.0 的 `HttpRequestNodeData` 要求 `method`、`url`、`authorization`、`headers` 和 `params`。无鉴权时使用：

```yaml
authorization:
  type: no-auth
  config: null
```

HTTP 节点继承的基础节点支持 `retry_config`，0.5.0 字段为 `retry_enabled`、`max_retries`、`retry_interval`。不要直接沿用其他版本的 `enabled`、指数退避等字段；无法确认时默认省略重试配置。不要因为移除了不兼容的重试配置而删除 `authorization`。

需要让网络异常继续执行时，可使用 `error_strategy: default-value`，并为 `body`、`status_code`、`headers` 提供类型匹配的默认值。真实异常不得用 2xx 或样本 JSON 冒充成功响应。保留可识别的失败状态并进入显式错误分支；样本响应只用于隔离的离线测试，不能写成业务节点的成功兜底。检查最终 DSL 是否超过目标版本的导入大小上限。

### Iteration

0.5.0 支持显式 `iteration-start`。该节点必须同时满足外层 `type: custom-iteration-start` 和内层 `data.type: iteration-start`，不能使用普通 `type: custom`。容器节点需设置 `start_node_id`；内部节点设置 `parentId`、`iteration_id`、`isInIteration: true`，内部边也要带相同的 `iteration_id`。`error_handle_mode` 可使用 `terminated`、`continue-on-error` 或 `remove-abnormal-output`。

### Template Transform

`template-transform` 的 `template` 会直接交给 Jinja2 沙箱执行。`variables` 中的 `variable` 是模板可见别名，`value_selector` 才指向上游值：

```yaml
type: template-transform
variables:
  - variable: result_json
    value_selector: [prepare_result, result_json]
template: "{{ result_json }}"
```

不得在 `template` 中写 `{{#prepare_result.result_json#}}`；该形式会被 Jinja2 解析并在 `#` 处报 `TemplateSyntaxError: unexpected char '#' at 2`。生成后必须核对每个 Jinja2 外部变量都在 `variables` 中声明，且没有引用后未声明的别名；声明后未使用属于清理提示，不是运行阻断项。字面示例及 Jinja raw/注释区域另行区分。

### LLM 与 Code

- LLM 支持 `context`、`vision` 和 `reasoning_format: tagged|separated`。
- LLM `prompt_template` 的 `edition_type: basic` 使用 `text` 和 Dify 引用；`edition_type: jinja2` 使用 `jinja2_text` 与 `prompt_config.jinja2_variables` 声明的别名。不要在 `basic` 活动文本中保留 `{% ... %}`，也不要在 Jinja2 活动文本中写 `{{#...#}}`。
- `completion_params` 的最终合法性由目标模型 provider 决定。优先复用同租户有效导出或读取 provider 参数 schema；缺少权威 schema 的新建任务可使用不超过两位小数的保守值；已有参数不自动舍入，高精度仅标为待核实，不能断言插件必然拒绝。
- Code 输出仅使用 string、number、object、boolean 及其常用数组类型；返回键必须与 `outputs` 一致。

## 降级检查

从 0.7.0 迁移到 0.5.0 时：

1. 深拷贝原文档并另存版本化文件，保留原 0.7.0 成果。
2. 把顶层 `version` 改为 `"0.5.0"`。
3. 只按本基线中的已知节点规则删除已确认不兼容的可选字段；不同版本形状的 HTTP `retry_config` 不得直接照搬。
4. 保留 HTTP `authorization`、Iteration Start 和容器元数据等旧版必需结构。
5. 比较降级前后文档，确认差异仅为版本和已核实的不兼容字段。
6. 分开报告结构错误与 `dependencies.unresolved`；不要为消除依赖告警猜写插件版本。
7. 未知节点或插件节点保持原样并标记未验证，不自动查询源码；除非完整兼容确认是本次明确要求的目标，或用户要求扩大检查范围。

## 校验边界

若本地校验器仅支持 0.6.0/0.7.0，不得把它的版本拒绝归因于 0.5.0 文件错误。默认按本基线和同版本字段核对；确需复用校验器的版本无关图检查时，只对临时副本操作并标明“替代版本的局部检查”，不能据此确认 0.5.0 schema 或导入兼容。`dify_import_compatible` 只有在目标版本源码字段核对或真实实例导入后才能确认，发布和运行状态必须分别验证。
