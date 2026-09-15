# Dify Workflow MCP

面向 Dify `1.16.1`、App DSL `0.7.0`、Graphon `0.6.0` 的独立 MCP Server。它复用 Dify 自带的自然语言工作流生成器，并通过真实控制台 API 创建或更新工作流草稿。

## 能力

- `dify_connection_check`：验证 Dify 连接和认证。
- `workflow_generate_dsl`：从自然语言生成完整 App DSL；传入 `app_id` 时基于现有草稿 refine。
- `workflow_validate`：离线校验 YAML、入口/终点、边端点、可达性、变量来源及秘密字段。
- `workflow_get_draft`：读取草稿摘要、稳定节点/边补丁路径和乐观锁 hash，不回传完整图或秘密值。
- `workflow_patch_draft_preview`：对当前草稿深拷贝执行显式、可逆的增量补丁，返回差异和固定预览，不写入 Dify。
- `workflow_patch_draft_apply`：仅在草稿 hash 和完整基线快照都未变化时应用固定补丁，并回读验证所有未指定字段保持不变。
- `workflow_browser_bridge_patch_preview`：从已登录浏览器同源读取的安全投影生成固定补丁，不接收任何 Cookie、Storage、鉴权头或受保护变量值。
- `workflow_browser_bridge_patch_apply_prepare`：再次锁定草稿 hash、页面原始投影指纹、传输归一化投影和受保护区哈希，只返回供页面执行的字段级补丁操作。
- `workflow_browser_bridge_patch_verify`：同时校验页面写前预期/写后回读原始指纹、传输归一化整图指纹和受保护区哈希。
- `workflow_create_draft_apply`：用已生成的 `preview_id` 创建新应用草稿。
- `workflow_update_draft_apply`：用 `preview_id + app_id + expected_hash` 覆盖现有草稿并回读验证。

生成与写入故意分成两步。`preview_id` 固定一次生成结果，避免预览后再次调用模型产生不同的图。预览默认保留 30 分钟，只存在 MCP 进程内存中。

本服务不提供发布工具。草稿可保存不代表插件、模型、知识库、Agent binding 或凭证已经满足发布与运行要求。

## Dify 端要求

### 默认：浏览器桥接

只配置 `DIFY_CONSOLE_API_URL`、未配置独立凭据时，MCP 自动进入 `browser_bridge` 模式：

```powershell
$env:DIFY_CONSOLE_API_URL = 'https://cloud.dify.ai/console/api'
```

桥接由 Codex 编排层完成：MCP 生成并校验补丁，已登录的同源浏览器页面执行 Dify GET/POST。STDIO MCP 进程本身不会也不能读取浏览器 Cookie；Cookie、Local Storage、Session Storage 和 Authorization/CSRF 头不会导出页面。

浏览器桥接协议当前为 v2。页面在 `graph/features` 穿过 CDP/MCP 传输边界前计算 `raw_projection_sha256`；MCP 另行计算传输归一化指纹。两者都必须在写前匹配，因此 Dify hash 未变但内容发生变化时仍会拒绝写入，也不会把浮点数跨进程序列化的末位差异误当作页面原始内容。

受保护的 `environment_variables` 和 `conversation_variables` 也不传给 MCP。页面仅传递它们的规范化 SHA-256；写入时从写前最新草稿原样合并，写后再次计算哈希。所有页面原始指纹的规范化规则为：对象键按 Unicode 字典序递归排序，数组顺序保持不变，UTF-8 JSON 不添加空白，然后计算 SHA-256。

如果 `graph/features` 含 `api_key`、`credential_id`、`authorization`、`access_token` 等敏感键，桥接会拒绝该草稿，必须使用下面的独立直连认证。这样可以避免租户凭据进入 MCP 工具参数或模型可见记录。

### 可选：独立直连认证

推荐为无头 MCP 连接启用 Dify 的 Admin API Key，并绑定目标工作区：

```powershell
$env:DIFY_CONSOLE_API_URL = 'https://dify.example.com/console/api'
$env:DIFY_ADMIN_API_KEY = '<admin-api-key>'
$env:DIFY_WORKSPACE_ID = '<workspace-id>'
$env:DIFY_GENERATOR_MODEL_PROVIDER = '<provider-id>'
$env:DIFY_GENERATOR_MODEL_NAME = '<model-name>'
```

Admin API Key 权限很高，应只放在 MCP 进程环境变量中，不要作为工具参数、DSL 字段或提示词传递。

也可使用短期控制台 JWT：

```powershell
$env:DIFY_CONSOLE_ACCESS_TOKEN = '<console-jwt>'
$env:DIFY_CSRF_TOKEN = '<matching-csrf-token>'
```

HTTPS 且没有 Cookie Domain 时，CSRF Cookie 名可能需要设为 `__Host-csrf_token`：

```powershell
$env:DIFY_CSRF_COOKIE_NAME = '__Host-csrf_token'
```

## 安装与运行

```powershell
Set-Location 'D:\dify-reference\dify-mcp'
uv sync
uv run dify-workflow-mcp
```

Codex MCP 配置示例：

```toml
[mcp_servers.dify-workflow]
command = "uv"
args = ["--directory", "D:/dify-reference/dify-mcp", "run", "dify-workflow-mcp"]

[mcp_servers.dify-workflow.env]
DIFY_CONSOLE_API_URL = "https://dify.example.com/console/api"
DIFY_ADMIN_API_KEY = "..."
DIFY_WORKSPACE_ID = "..."
DIFY_GENERATOR_MODEL_PROVIDER = "..."
DIFY_GENERATOR_MODEL_NAME = "..."
```

浏览器桥接的最小配置无需密钥：

```toml
[mcp_servers.dify-workflow.env]
DIFY_CONSOLE_API_URL = "https://cloud.dify.ai/console/api"
```

修改 `config.toml` 后需要重启 Codex，使新 MCP 进程读取配置。

## 推荐调用顺序

创建新工作流：

1. 调用 `workflow_generate_dsl`，检查 YAML、节点统计和 `validation`。
2. 处理 `unresolved_dependencies`，确认目标工作区已有对应模型与插件。
3. 将返回的 `preview_id` 传给 `workflow_create_draft_apply`。
4. 只有 `draft_synced=true` 才表示导入后图回读一致。

修改现有工作流：

1. 调用 `workflow_get_draft(app_id)` 获取当前 hash。
2. 调用 `workflow_generate_dsl(..., app_id=...)` 生成 refine 预览。
3. 检查返回的 `base_hash` 与第一步一致。
4. 调用 `workflow_update_draft_apply(preview_id, app_id, expected_hash)`。
5. 如果返回 `conflict`，说明草稿已被其他用户修改，必须重新生成预览。

精确增量修改：

1. 调用 `workflow_get_draft(app_id)` 获取 `draft_hash` 和 `patch_inventory`。
2. 调用 `workflow_patch_draft_preview(app_id, operations)`。路径以草稿 workflow 为根，支持 `add`、`replace`、`remove`、`test`。
3. 优先用稳定 ID 选择器定位节点和边，例如：

```json
[
  {
    "op": "test",
    "path": "/graph/nodes/@llm-node/data/model/name",
    "value": "Qwen3.6-35B"
  },
  {
    "op": "replace",
    "path": "/graph/nodes/@llm-node/data/model/completion_params/temperature",
    "value": 0.2
  }
]
```

4. 检查 `changes`、`validation` 和 `invariants.unspecified_fields_unchanged=true`。
5. 将 `preview_id + app_id + base_hash` 传给 `workflow_patch_draft_apply`。
6. 只有 `draft_synced=true` 且 `unspecified_fields_unchanged=true` 才表示线上回读与固定补丁完全一致。

补丁路径也支持标准 JSON Pointer 数组索引和最终 `-` 追加，但节点、边优先使用 `@<id>`，避免并发调整导致索引漂移。补丁拒绝整段替换/删除、认证字段和环境变量值；未指定内容通过基线快照、正反向回放、乐观锁和完整回读指纹四层校验。

浏览器桥接增量修改：

1. 在已登录、与 `DIFY_CONSOLE_API_URL` 同源的页面上下文中读取应用和草稿；先在页面内扫描 `graph/features` 是否含敏感键。
2. 页面保留完整草稿，并在页面内计算 `raw_projection_sha256=SHA256({graph,features})` 与受保护区 SHA-256；只把安全的 `graph/features` 投影、两个哈希、草稿 `hash` 和应用 `mode` 传给 `workflow_browser_bridge_patch_preview`。
3. 用户确认预览后，在页面内重新读取草稿；把最新 hash、投影、`current_raw_projection_sha256` 和受保护区哈希传给 `workflow_browser_bridge_patch_apply_prepare`。Dify hash、页面原始指纹、传输归一化指纹或受保护区任一不一致都会返回 `conflict`，且不写入。
4. `apply_prepare` 返回 `page_patch.operations`，不再返回完整 `graph/features`。在一个页面上下文操作中再次 GET 并校验三种基线，在这个最新原始对象上执行 `test/add/replace/remove`；`test` 失败立即中止。禁止用经过 MCP/CDP 传输的整图替换页面原始图。
5. 页面在 POST 前计算字段级补丁后的 `expected_raw_patched_projection_sha256`，并把页面内原样保留的 `environment_variables/conversation_variables` 带入请求。浏览器自动携带同源会话，不导出页面凭据。
6. 页面重新 GET 草稿并计算 `readback_raw_projection_sha256`；将其连同 POST 前预期原始指纹、传输归一化投影、受保护区哈希和新 hash 传给 `workflow_browser_bridge_patch_verify`。只有原始指纹、归一化指纹和受保护区哈希全部匹配，才返回 `draft_synced=true` 与 `unspecified_fields_unchanged=true`。

浏览器页面上下文只是认证传输层，不扩大操作授权；读取授权不能自动升级为修改、发布、运行或删除授权。

## 安全和兼容边界

- 只支持 `workflow` 和 `advanced-chat`。
- 新建 DSL 固定输出带引号的 `version: "0.7.0"`。
- 可移植 YAML 会清除凭证 ID、Token、Authorization 等字段，并清空 secret 环境变量值。
- 修改现有草稿时，秘密值只在 MCP 内存和 Dify API 请求中保留，不出现在工具结果里。
- 浏览器桥接时，秘密值只留在浏览器页面上下文；MCP 仅接收受保护区 SHA-256。
- 浏览器桥接 v2 只把显式字段补丁传回页面；页面从最新原始草稿开始应用，不使用 MCP 返回的整图覆盖草稿。
- 增量补丁不能修改环境变量值、Token、凭证或 Authorization 等敏感字段。
- `dependencies: []` 是合法完整外壳，但模型、Tool、Knowledge 或 Agent 节点仍可能需要在目标工作区补齐依赖。
- 创建使用 `POST /console/api/apps/imports`；修改使用带 hash 的 `POST /console/api/apps/{id}/workflows/draft`；写入后都重新读取草稿并比较图指纹。
- Dify 返回 202/pending 时不会自动确认版本跨级导入。
- 不会自动发布、运行工作流或调用外部副作用工具。

## 测试

```powershell
uv run pytest
```
