# Dify 1.16.1 / DSL 0.7.0 基线

## 版本

- Dify：`1.16.1`
- App DSL：`0.7.0`
- Graphon：`0.6.0`
- Python：Dify 要求 `~=3.12.0`

源码入口：

- `api/constants/dsl_version.py`
- `api/services/dsl_version.py`
- `api/services/app_dsl_service.py`
- `api/services/workflow_service.py`
- `api/core/workflow/generator/runner.py`
- Graphon `src/graphon/variables/variable_pool.py`

## 顶层结构

```yaml
version: "0.7.0"
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

导入 YAML 根必须是 mapping，且必须有 `app`。覆盖导入只支持 `workflow` 与 `advanced-chat`。版本兼容和确认状态应以该目标实例导入器的返回为准；不要将其他版本的导入策略外推到当前实例。

## 变量

基础选择器由节点 ID 和输出名组成；嵌套字段选择器按目标版本及变量类型核对，不把所有多段路径直接判错：

```yaml
value_selector: [source_node, output_name]
```

模板引用：

```text
{{#source_node.output_name#}}
```

保留命名空间：`sys`、`env`、`conversation`、`rag`。工作流级环境和会话变量支持 string、secret、number/integer/float、boolean、object 及常用数组，不支持 file。

常见固定输出：

- LLM：`text`
- Code：`outputs` 声明的键
- HTTP：`body`、`status_code`、`headers`、`files`
- Knowledge Retrieval：`result`
- Template/Iteration/Loop/Variable Aggregator：`output`
- Document Extractor：`text`
- List Operator：`result`、`first_record`、`last_record`

Tool、Datasource 与 Agent 输出依赖动态元数据，离线不可完整推断。

## 容器与版本别名

- 前端 `agent-v2` 在运行 DSL 中写作 `data.type: agent`、`data.version: "2"`。
- 旧 `variable-assigner` v1 与新 `assigner` v2 不是同一节点。
- `start-placeholder` 与 `datasource-empty` 是编辑器占位符，不应进入运行图。
- Iteration/Loop 的内部节点通过 `parentId` 和容器元数据关联；普通外层可达性算法不能把内部节点误报为断开。
- 容器起始节点使用专用外层 wrapper：`iteration-start` 对应 `custom-iteration-start`，`loop-start` 对应 `custom-loop-start`。普通业务节点才使用 `custom`；生成器和校验器必须同时检查外层 `type` 与 `data.type`。

## 校验边界

`WorkflowService.sync_draft_workflow` 的结构校验是轻量的；发布会额外检查工具、模型凭证、Agent binding 与触发器限制；Graphon 构图和节点执行仍会发现运行时问题。因此至少区分：静态有效、导入兼容、草稿保存、发布就绪、运行通过。
