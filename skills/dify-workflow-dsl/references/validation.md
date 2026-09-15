# 验证步骤与报告契约

## 最终产物检查

1. 读取最终落盘 YAML/JSON，拒绝重复 mapping key、空文档和畸形节点。保留目标 DSL 版本；Graph JSON 检查不能代替完整 App DSL 检查。
2. 对照对应版本基线核对顶层、运行节点字段与 wrapper；未知节点列入未验证清单。可用同版本源码校验器时优先使用，并记录版本/路径。Skill 自带脚本不是完整 DSL schema 校验器。
3. 检查 ID 唯一、边端点存在、source handle、入口/终点和路径可达性；容器内部从容器入口检查，不当作外层孤岛。检查容器归属与输出边界。
4. 逐个检查选择器、活动提示词插值、输出类型和作用域。固定输出参考版本基线；动态工具/Agent 输出缺少元数据时标 `unknown`，不能仅凭常见 `text` 推定已验证。
5. 运行下方静态门禁；涉及 Code 变更时执行 [运行时门禁](runtime-guardrails.md) 中的编译和安全回放。
6. 比较变更前后语义差异：Start/End 契约、模型/工具绑定、依赖、环境/会话变量、未修改的提示词和布局。只保留获授权或必要的变更。检查最终文件后再计算 hash；后续写入使原检查和 hash 失效。

## 自带扫描器

从当前 Skill 位置解析脚本绝对路径，不能假定 cwd 是 Skill 根目录。下面 `SKILL_DIR` 和 `WORKFLOW_PATH` 是需替换的路径占位符：

```powershell
uv run --with pyyaml --with jinja2 python -X utf8 -B "SKILL_DIR/scripts/validate_runtime_guards.py" --require-jinja2 "WORKFLOW_PATH"
```

已有依赖时也可用 `python -X utf8 -B`。没有 uv 时使用已有 Python 环境；依赖安装失败时说明缺口，不将失败归因于业务 YAML。

脚本只读，不执行节点代码、不访问网络、不写入 Dify。它检查输入基本形状、重复键/ID、容器起始 wrapper、Start JSON 默认值类型、Jinja 活动字段/别名、basic 提示词可疑语法及模型数值/精度风险；**不检查完整 schema、所有边和选择器、Code 执行、真实分支覆盖、插件 schema 或租户可用性**。

- 退出码 `0`：本脚本覆盖范围内无 error；仍需读 `issues` 中的 warning 和 `coverage`。
- 退出码 `1`：发现门禁错误。
- 退出码 `2`：读取、解析或输入形状错误；与节点运行失败分开报告。
- `JINJA2_UNAVAILABLE`：本地模板解析未完成；基础扫描不能替代完整 Jinja 检查。
- `LLM_BASIC_JINJA_SYNTAX`：需人工判断字面示例还是待执行模板，不能见到花括号就改写用户内容。
- `MODEL_PARAM_PRECISION`：可移植性待核实，不证明 provider 拒绝。核对后记录依据；只有 schema 允许才用 `--allow-high-precision-model-params`。此开关对整个文件生效，因此必须核对全部触发参数。

warning 不等于可以忽略。逐项记录“已确认为字面量/已获 provider 证据”或“待核实”；确认属于真实动态模板错误时必须修复，无法消除的不确定性应反映到相关验证状态。

Skill 维护后运行回归：

```powershell
uv run --with pyyaml --with jinja2 python -X utf8 -B -m unittest discover -s "SKILL_DIR/scripts" -p "test_*.py" -v
```

## 证据记录

按需要写入工作目录的验证报告，不把业务样本和运行日志放进 Skill。每项检查记录：文件/hash、检查工具或人工核对依据、目标版本、结果、问题节点/字段、未覆盖范围。沿用 SKILL.md 的六个状态；Code 编译/回放独立记录，不混入 `graph_valid`。

局部修复可确认已修改字段通过；整体状态不能因为局部检查成功而设为 true。模型元数据未取得、某版本不受校验器支持、Jinja2 缺失和未运行均为覆盖缺口；只有已发现错误才填 false。

## 线上动作（仅在已授权时）

1. 定位准确实例、工作区和 App/provider，读取最新基线并保存可恢复副本。
2. 构造最小变更，验证后写入。若 hash/指纹变化，重新读基线并计算差异；不要盲目重放旧补丁或覆盖别人修改。
3. 写入响应失败或超时先回读，确认是否已生效；创建操作先查重。确定未生效后才考虑重试；无法判断时停止该写操作并报告。
4. 回读验证字段、节点/边与保护项；不一致时不继续发布或运行。不要擅自回滚覆盖可能存在的并发更新。
5. 测试页面可能自动保存；测试后再次回读，用最新 hash 报告。发布、运行、只打开测试面板分别记录，不相互推导。
