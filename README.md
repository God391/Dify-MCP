# Dify MCP 与 Workflow DSL Skill

用于生成、检查和安全更新 Dify 工作流草稿的 MCP Server，以及配套的 Codex 工作流技能。

## 目录

- `dify-mcp/`：MCP 源码、测试、`uv.lock` 和环境变量示例。
- `skills/dify-workflow-dsl/`：Skill 指令、版本基线、运行时门禁、校验脚本及通用模板。
- `codex/mcp-config.toml.example`：只转发环境变量名的 MCP 注册模板。

仓库不包含实际凭证、业务工作流、虚拟环境或缓存。Skill 内的 YAML 为通用示例模板。

## 版本边界

| 组件 | 支持范围 |
|---|---|
| MCP 0.3.0 | Dify 1.16.1 / App DSL 0.7.0 / Graphon 0.6.0 |
| Workflow DSL Skill | 已有文件保留其版本；无版本的新建任务默认 Dify 1.11.x / DSL 0.5.0，另提供 0.7.0 基线 |

Skill 的 0.5.0 支持不意味着 MCP 的 0.7.0 生成器或校验器支持 0.5.0。校验器拒绝目标版本时，按对应 Skill 基线检查，不修改业务文件版本来绕过检查。

## 快速开始

需要 Python 3.12+ 和 `uv`。以下使用 PowerShell 7 (`pwsh`)；示例从仓库根目录开始。

```powershell
git clone git@github.com:God391/Dify-MCP.git
Set-Location Dify-MCP
Set-Location dify-mcp
uv sync --locked
uv run pytest
```

### 配置 MCP

将 [配置模板](codex/mcp-config.toml.example) 合并到 Codex 配置，把 `<仓库绝对路径>` 替换为实际目录；不要覆盖其他 MCP 配置。示例路径可用正斜杠 `D:/Tools/Dify-MCP`。

默认浏览器桥接模式仅需要目标控制台 API 地址；下面地址示例适用于 Dify Cloud：

```powershell
[Environment]::SetEnvironmentVariable('DIFY_CONSOLE_API_URL', 'https://cloud.dify.ai/console/api', 'User')
```

保存配置后重新启动 Codex，使进程读取配置和环境变量。浏览器桥接需要可用的浏览器工具和已登录的同源 Dify 页面，由使用方编排；MCP 不读取或导出浏览器 Cookie/Storage。

无头直连认证、模型参数及完整工具说明见 [MCP 文档](dify-mcp/README.md) 和 [环境变量示例](dify-mcp/.env.example)。真实密钥只放在本机私有环境中。项目没有自动加载 `.env` 的保证，应将配置注入 MCP 进程环境。

### 安装 Skill

回到仓库根目录，运行：

```powershell
$sourceSkill = Join-Path (Get-Location) 'skills/dify-workflow-dsl'
$codexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$targetSkill = Join-Path $codexDir 'skills/dify-workflow-dsl'
if (Test-Path -LiteralPath $targetSkill) {
    throw '目标 Skill 已存在，请先比较或备份旧版本。'
}
New-Item -ItemType Directory -Path (Split-Path $targetSkill) -Force | Out-Null
Copy-Item -LiteralPath $sourceSkill -Destination $targetSkill -Recurse
```

在 Codex 中使用 `$dify-workflow-dsl`。该 Skill 包含版本选择、接口保护、JSON 内部传输与外部对象出口、模板语法、容器及分层验证规则。

## 测试

在 `dify-mcp/` 中执行 MCP 测试：

```powershell
uv run pytest
```

在仓库根目录执行 Skill 回归：

```powershell
uv run --with pyyaml --with jinja2 python -X utf8 -B -m unittest discover -s skills/dify-workflow-dsl/scripts -p 'test_*.py' -v
```

扫描自己的工作流：

```powershell
uv run --with pyyaml --with jinja2 python -X utf8 -B skills/dify-workflow-dsl/scripts/validate_runtime_guards.py --require-jinja2 '<工作流路径>'
```

扫描输出的 `ok` 仅表示该脚本未发现 error。仍需查看 `issues` 和 `coverage`，以及 [验证步骤](skills/dify-workflow-dsl/references/validation.md) 中未被脚本覆盖的检查。

## 操作与验证边界

MCP 的生成/预览与实际写入分离，通过固定预览、草稿 hash、字段补丁和回读校验保护现有配置。写入必须在用户授权范围内执行；本服务不提供发布或运行工具。

分别报告 schema、图、导入兼容、草稿同步、发布就绪和运行结果；本地测试通过不证明目标租户的模型、工具、知识库、凭证或真实运行可用。内部可以按需传 JSON 字符串，外部接口仍按既定契约提供对象或字符串。
