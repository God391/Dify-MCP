# 复杂工作流分析：Automated Email Reply

## 来源与选择理由

样本来自 Dify `1.16.1` 标签中的 `api/constants/recommended_apps.json`，应用 ID 为 `e9d92058-7d20-4904-892f-75d90bef7587`，名称为 `Automated Email Reply`。

源码地址：<https://github.com/langgenius/dify/blob/1.16.1/api/constants/recommended_apps.json>

历史分析时提取的原始 YAML 位于 `D:/dify-reference/analysis/samples/automated-email-reply-legacy.yml`，SHA-256 为 `A192F70F543918A09E19D983B25EB0AD307408A0F41A6D45BAE1B8E6872B6E36`。它仅用于历史结构分析，不是默认兼容母版，也不是运行 Skill 的必需文件。

它覆盖外层工具调用、Code、Iteration、内部工具链、多个 LLM、Question Classifier、Variable Aggregator、Template 和 Answer，适合作为复杂拓扑的观察样本。

## 结构

- 模式：`advanced-chat`
- 画布节点：23
- 边：19
- 运行节点类型：Start、Answer、Code ×3、Iteration、Tool ×3、LLM ×4、Question Classifier、Variable Aggregator、Template Transform ×4
- 另有 4 个 `data.type` 为空的画布注释节点
- 主链：Start → Gmail 列表工具 → Code 提取邮件 ID → Iteration → 汇总模板 → Answer
- Iteration 内：获取单封邮件 → 提取/解码 → 元数据处理 → 分类 → 两条回复生成分支 → 聚合 → 编码/构造请求 → 创建 Gmail 草稿

Iteration 内部节点不会通过普通外层边从 Start 直接到达；应结合 `parentId`、`iteration_id` 和 iteration start 语义分析，不能简单标为孤立节点。

## 可复用模式

1. 外层一次获取 ID 数组，内层逐项处理，避免把整批对象塞入单次 LLM。
2. 在容器内用分类器分流，再用 Variable Aggregator 合并同类型结果。
3. Code 负责结构解析，Template 负责最终请求或报告整形。
4. 外层只消费 Iteration 的统一数组输出，隐藏内部实现。

## 不能直接作为通用母版的原因

- 导出内容没有 `version`、`kind`、`dependencies`，属于历史推荐模板格式。
- Gmail 工具使用 `provider_type: api` 和工作区本地 UUID，不可跨租户移植。
- OpenAI provider 使用旧名称，模型配置不满足当前所有节点实体的完整字段要求。
- Iteration 使用旧式 `isIterationStart` 表达，没有当前规范的显式 `iteration-start` wrapper。
- 4 个注释节点的 `data.type` 为空，运行校验不能把它们当普通节点。
- Base64 编解码由 LLM 执行，结果不确定且成本高；应改成 Code。
- `createDraft` 是外部副作用，必须与纯生成/分析步骤分离，并在执行前获得授权。

## Skill 模板的改造

`assets/templates/complex-feedback-analysis-0.5.yml` 只复用抽象拓扑，不复制原提示词或租户工具配置：

- 改为默认兼容下限 DSL `0.5.0` 的完整外壳。
- 用 Code 确定性拆分输入和统计风险。
- 用 If/Else + 两个 Template + Variable Aggregator 表达分支合并。
- 用显式 Iteration Start + 容器内 LLM 逐条分析。
- 移除 Gmail、凭证、租户 UUID 与外部写操作。
- 仅保留可声明的 OpenAI marketplace dependency，导入后仍需在目标工作区选择可用模型。

该模板适合学习复杂结构和作为生成起点；它不代表目标工作区的模型凭证已经可用。

## 校验器边界

历史 0.7.0 改造样本曾通过 Dify 1.16.1 节点模型及独立图检查；这不构成当前随包 0.5.0 模板的同版本兼容证据，必须对当前文件重新检查。Graphon 0.6.0 的 `graphon.dsl.inspect` 会因 `iteration`、`iteration-start` 返回 `unsupported`；该 slim DSL loader 不覆盖容器节点，而 Dify 实际注册并接受这些节点。不要把这一结果误报为 Dify App DSL 无法导入。
