# `json_object` 测试运行渲染门禁

## 适用范围

本规则针对 Dify 1.11.x / App DSL `0.5.0` 的 `workflow` Start 输入，尤其是用户点击“测试运行”后立即出现“渲染此组件时发生了意外错误”的场景。

## 已验证的故障链

1. Start 变量使用 `type: json_object`，且 DSL 将空默认值写成 YAML 对象 `default: {}`。
2. Dify 1.11.x 的测试运行表单把该默认值原样传入 JSON `CodeEditor`。
3. `CodeEditor` 再把值原样交给 Monaco Editor；Monaco 的模型创建路径只接受字符串或文本快照。
4. 收到普通 JavaScript 对象时，Monaco 会尝试调用对象的 `.create`，产生 `TypeError: $.create is not a function`，页面进入错误边界。

这是前端测试输入面板的渲染失败，发生在工作流提交和节点运行之前；它不是 Code、LLM、Iteration 或 HTTP 节点的运行时错误。模型检查清单也必须单独判断，不能用模型未配置解释这个错误。

## 生成规则

- 保持 `type: json_object`，不要为了规避编辑器错误把外部字段改成必填字符串。
- 空默认值必须是 JSON 文本字符串：`default: '{}'`。禁止使用未加引号的 `default: {}`。
- 非空对象默认值也应先 `JSON.stringify` 成字符串再写入 DSL；生成器应断言 `json_object` 默认值的 Python 类型为 `str`。
- Dify Start 运行时会把合法 JSON 文本恢复为对象，因此该修复不改变外部对象输入契约；兼容适配器仍可接收和输出原生对象。
- `json_schema` 是校验 schema，不等同于输入默认值。除非目标 UI 版本明确要求，否则保留后端接受的 schema 对象；不要用修改 schema 来掩盖默认值类型错误。

## 必做验证

### 本地生成

- 存在维护中的生成器时修改生成器并重新生成 YAML/图 JSON；否则直接修复当前文件，并在最终落盘文件上验证。
- 检查 Start 的每个 `json`/`json_object` 默认值：若存在，必须为字符串。
- 运行 YAML/图往返检查和 `validate_runtime_guards.py`；仅在 Code 或其入参受影响时补充编译与安全回放；这些检查不能替代浏览器测试面板验证。

### 目标实例

在得到目标实例写入授权后，按“最新草稿 GET → 字段级补丁 → POST → GET 回读”的顺序，仅修改目标变量的 `default` 字段。刷新全新页面后只打开“测试运行”面板，不提交执行；确认不再出现错误边界，并记录控制台是否仍有 `$.create is not a function`。不得把该只读渲染检查当成已完成的工作流运行验收。

### 诊断分层

- 点击测试运行即进入错误边界，且无运行请求：优先检查输入变量默认值和编辑器值类型。
- 面板能打开，但提交后提示模型/凭证/工具问题：归类为 `publish_ready` 或 `runtime_ready` 阻断，不归类为组件渲染错误。
- 新页面正常而旧页面仍报错：按旧标签页残留处理，先关闭或刷新旧页面，不覆盖正确草稿。

## 不应泛化的结论

这是 Dify 1.11.x 测试表单把 `json_object` 值直接交给 Monaco 的兼容门禁。后续版本可能在 UI 层自动字符串化，但将 DSL 默认值保持为 JSON 字符串仍是跨版本更稳妥的表示；是否修改 `json_schema` 必须以目标版本源码或实例回读为依据。
