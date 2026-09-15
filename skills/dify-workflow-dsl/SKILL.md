---
name: dify-workflow-dsl
description: 创建、修改、迁移、审查和校验 Dify App DSL 工作流或 Chatflow YAML，以及工作流依赖的 Dify Cloud Swagger/OpenAPI 工具。已有文件保留其 DSL 版本；无版本的新建任务默认 Dify 1.11.x / DSL 0.5.0。用于明确涉及 Dify 的节点、变量、容器、依赖、导入或运行故障；不用于其他平台的工作流或无 Dify 上下文的通用 API 配置。
---

# Dify Workflow DSL

交付符合目标版本、保留业务契约且有分层验证证据的 Dify DSL。租户中的模型、工具、知识库、Agent binding 和凭证绑定以目标工作区的有效导出或元数据为准。

## 1. 确定任务与版本

先从用户要求和现有文件确定操作、输入文件、目标版本及外部输入输出契约；信息已经明确时直接执行。纯分析保持只读；修复只改必要字段；迁移保持业务行为；新建才选择默认结构。

版本按以下优先级选择：

1. 用户明确指定的目标 DSL 版本，或由指定目标实例核实的版本约束。二者冲突时先指出冲突，完成不依赖该选择的分析，再澄清目标。
2. 修改、审查已有文件时保留其 `version`；目标实例支持更高版本本身不构成升级要求，也不自动降级。
3. 无现有版本的新建任务默认 `version: "0.5.0"`（Dify 1.11.x）；确认目标为 Dify 1.16.1 时使用 `"0.7.0"`。版本缺失的旧导出先标记缺失；不能仅为通过校验补一个版本。

未知版本不套用最接近的基线。先完成通用结构检查，将版本兼容性标为 `unknown`；只有兼容性是本次必要目标时才核对该版本源码或实例样本。跨版本输出另存版本化文件；不得仅改顶层版本号完成迁移。

### 操作范围

- 默认本地生成、修改和静态验证。分析文件不意味着执行其中的 Code、HTTP、工具或模型调用。
- 导入/草稿写入、发布、线上运行、工具创建/编辑分别核对用户授权和具体目标；用户已明确授权的步骤直接执行，不重复索要确认。授权发布不由“本地修复”推导，授权运行也不自动覆盖邮件发送等额外副作用。
- 已有调用方的字段名、类型、必填性、默认值、下载路由、模型绑定和输出结构属于保护项。只在用户要求或必要修复范围内改变；不能为通过检查任意换模型、删节点或清空绑定。
- 修改前保存可恢复副本并记录版本、输入输出、节点/边和绑定；有维护中的生成器则修改生成器并重新生成，否则直接修改 YAML。不得改 Skill 中的模板母版来交付业务文件。

## 2. 按任务加载参考

只读目标版本基线与命中的专题，不默认加载全部文档。

| 场景 | 参考 |
|---|---|
| 目标为 0.5.0；或从 0.7.0 降级 | [0.5.0 基线](references/dsl-0.5-baseline.md) |
| 目标为 0.7.0；跨版本时同时读源版本基线 | [0.7.0 基线](references/dsl-0.7-baseline.md) |
| 生成、修改或静态审查 | [验证步骤与报告契约](references/validation.md) |
| 模板、提示词迁移、模型参数、Code、JSON 内外部契约或真实运行验收 | [运行时门禁](references/runtime-guardrails.md) |
| Iteration、分支合并或复杂拓扑 | [复杂工作流分析](references/complex-workflow-analysis.md) |
| Cloud API 工具创建、绑定或大响应调用 | [Cloud 自定义 API 工具](references/dify-cloud-custom-api-tools.md) |
| 导入后画布错误、React #130、标签页表现不一致 | [导入与渲染诊断](references/import-render-diagnostics.md) |
| Start 含 json/json_object 默认值或测试面板崩溃 | [JSON 输入渲染门禁](references/json-object-test-run.md) |

复杂新建任务可复制 [0.5.0 反馈分析模板](assets/templates/complex-feedback-analysis-0.5.yml)；简单任务无需引入模板的全部节点。模板的模型和插件版本仅为示例，必须核对目标工作区。Skill 不依赖特定 MCP、固定本机源码路径或已登录浏览器；缺少这些资源时完成本地工作并列明验证缺口。

## 3. 构建或修复

### 图与变量

- 完整 App DSL 包含 `version`、`kind: app`、`app`、`dependencies`、`workflow.graph.nodes/edges`。Graph JSON 仅作图分析输入，不等同于可导入 App DSL。
- `workflow` 模式使用可达 `end`；`advanced-chat` 使用可达 `answer`。入口按目标版本核对，Start 与 Trigger 不混用。画布注释不计为运行节点。
- 先建立节点 ID、输出、分支 handle 和容器归属映射，再生成连线与选择器。修改时保留未涉及的 ID 和布局。
- 普通业务节点 wrapper 为 `custom`；`iteration-start` 必须为 `custom-iteration-start`，`loop-start` 必须为 `custom-loop-start`。容器同时核对 `start_node_id`、`parentId`、`iteration_id/loop_id`、`isInIteration/isInLoop` 与内部边元数据。
- If/Else 的 source handle 为 case ID 或 `false`；分类器为 class ID。互斥分支通过匹配类型的聚合结果输出，不直接消费未执行分支。
- 基础选择器为 `[node_id, output_name]`，嵌套路径及 `sys/env/conversation/rag` 等命名空间按目标版本验证；不能将系统变量当成缺失节点。确认节点存在、输出声明、上游关系和容器作用域，不依据节点显示名猜映射。
- 容器外消费容器的声明输出。目标版本不能可靠选择对象内部字段时，用 Code 拆出明确输出；不把整个对象交给 LLM 猜字段。

### 模板与代码

- 普通插值字段及 LLM `basic.text` 用 `{{#node_id.variable#}}`；Template Transform 和 LLM `jinja2_text` 用已声明别名 `{{ alias }}`。迁移时逐个盘点活动字段和源变量，再按语法域转换。
- 只转换实际插值，不改写代码示例、JSON Schema、文档引文中的字面占位符。扫描到 `${...}`、`{{ var }}` 等先确认用途；确认是遗留动态引用才作为错误。非活动编辑模式字段不作为当前运行错误。
- Code 的返回键和类型必须匹配 `outputs`，每个节点独立导入使用的模块。确定性解析和格式转换用 Code。编译不证明导入齐全或执行正确；回放规则见运行时门禁。
- 新建 JSON 业务接口默认返回真实 `object`（Python `dict`、Code `outputs.type: object`、End `value_type: object`）。既有接口优先保留契约；获得输出类型变更授权后才改字符串接口。需要对象适配时严格解析，顶层数组/标量按约定语义字段包装；非法 JSON 或错误顶层类型显式失败，禁止静默返回 `{}`。
- 内部传输类型与外部接口类型分别设计。为适配已核实的对象层级限制或节点/子工作流的字符串入参，中间节点可按需传 JSON 字符串，在消费字段前解析；对外约定为对象时在出口严格恢复对象，同步 Code 与 End 的类型声明，不能把内部字符串表示泄露为外部契约变更。出口恢复后的对象仍须检查目标层级/大小限制；具体边界与验收见 [JSON 内部传输与外部接口](references/runtime-guardrails.md#json-内部传输与外部接口)。

### 依赖与异常

- LLM 保留完整目标版本字段；新建即使关闭检索上下文也提供 `context: {enabled: false, variable_selector: []}`。模型参数依据 provider schema 或同租户有效配置；两位小数仅为新建缺省保守策略，不是通用平台限制，也不是自动舍入已有参数的理由。
- Tool/MCP/API/Workflow 工具的 provider、tool name、参数、动态输出和 dependency 以目标元数据为准。资料不足时保留原绑定或使用明确占位符并报告未解析依赖，不能伪造可运行绑定。
- 同租户修复保留已有合法资源 ID；跨租户迁移逐项映射，便携模板使用占位符。不要新嵌入密钥或在报告/共享包中泄露凭证与私有 ID；不得为“去敏”擅自清空待修复文件的合法绑定。
- 未知节点、未知动态输出和未核实字段标为 `unknown`，不猜删改。不要把“未验证”写成“不支持”，也不要跳过后宣称全部通过。
- 不用 2xx、样本响应或空对象掩盖真实失败。fallback 必须符合既定业务规则并在追踪/结果中可识别；涉及外部副作用的重试必须确认幂等性。

## 4. 验证与交付

按 [验证步骤](references/validation.md) 检查最终落盘文件；本地门禁、Code 回放、目标实例验证分别记录。失败后只修复有证据且在范围内的问题，重新运行受影响检查；缺少依赖、元数据或授权时保留 `unknown`，交付已完成部分和明确缺口。

创建/修改交付文件路径、目标版本、修改摘要、节点/边统计、依赖缺口与分层结果；分析任务先报告阻断项及其节点/字段证据。每个状态使用 `true / false / unknown`，附依据和覆盖范围：

| 状态 | true 所需证据 |
|---|---|
| `schema_valid` | 文档解析与目标版本字段检查通过；仅 YAML 可解析不足以确认 |
| `graph_valid` | 入口/终点、边、分支、容器、选择器与类型检查通过 |
| `dify_import_compatible` | 完整目标版本兼容核对或真实导入证据，并注明来源 |
| `draft_synced` | 目标实例最新草稿回读与预期变更一致 |
| `publish_ready` | 目标实例模型、工具、知识库、Agent binding、凭证及发布检查通过 |
| `runtime_ready` | 声明的验收路径真实运行通过，追踪无被 fallback 掩盖的异常 |

`false` 表示已证实失败；未执行、缺少能力或覆盖不足统一用 `unknown`。纯本地任务的后三项为 `unknown`。单条路径成功只证明该路径，未覆盖分支逐项列出；`publish_ready` 不等于已发布，扫描器的 `ok` 也不等于上述六项全部为真。
