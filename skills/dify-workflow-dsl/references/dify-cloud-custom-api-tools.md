# Dify Cloud 自定义 API 工具

用于在 Dify Cloud 中把外部 HTTP API 作为可复用工具配置，并在工作流中安全绑定。当前 Cloud 中文界面的入口是“集成 → 工具 → Swagger API 作为工具”。页面名称可能变化，操作前以当前可见导航为准。

## 操作边界

- 创建或编辑工具、点击工具内“测试”、绑定工作流、保存工作流草稿、发布和运行是独立动作，只执行用户明确要求的项。已有授权有效时完成基线核对后直接执行，无须逐步再次确认。写入失败、并发变化和回读规则见 [验证步骤](validation.md)。
- 使用当前已登录页面，不读取、复制或导出 Cookie、Storage、认证请求头或其他会话材料。
- 创建前读取当前工具列表，避免同名 provider 或重复 `operationId`；创建后必须从列表和详情页回读。
- OpenAPI 中的凭据只声明鉴权方式，不写入 Schema；密钥在 Dify 的鉴权配置中录入。没有明确授权时不要创建或保存持久凭据。

## Schema 约束

优先使用目标 Cloud 当前能解析的 OpenAPI 版本；需要兼顾旧版 Dify 时使用 `openapi: "3.0.0"`。

- `servers[].url` 必须是普通 URL，不能是 Markdown 链接或带显示文本的字符串。
- 每个操作提供稳定且唯一的 `operationId`。Dify 将它作为 Action/tool name，后续工作流绑定依赖该标识。
- 路径参数同时出现在路径模板和 `parameters` 中，并设置 `in: path`、正确类型及 `required: true`。
- `responses.content` 应与服务真实 `Content-Type` 和响应结构一致。只修改 OpenAPI 声明不会改变服务端响应头。
- Dify Cloud 必须能从公网访问 `servers.url`；优先 HTTPS。企业内网、VPN、仅集群 DNS 或本机地址通常不可达。
- Schema 导入成功只证明结构可解析，不证明网络、鉴权、响应格式或响应体积可运行。

最小示例：

```yaml
openapi: "3.0.0"
info:
  title: 文件 JSON 获取工具
  version: "1.0.0"
servers:
  - url: https://api.example.com
paths:
  /files/{fileId}:
    get:
      operationId: GetFileJson
      summary: 获取文件 JSON
      parameters:
        - name: fileId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: 文件 JSON
          content:
            application/json:
              schema:
                type: object
                additionalProperties: true
```

## Cloud UI 创建与回读

1. 打开“Swagger API 作为工具”，先检查是否已存在同名工具。
2. 选择创建，填写 provider 显示名称和 Schema；鉴权、标签、隐私协议与免责声明只按用户要求配置。
3. 在保存前检查“可用工具”表格已解析出预期 `operationId`、描述、HTTP 方法和路径；解析结果不符时不要保存。
4. 保存后要求出现明确成功信号，并在工具列表看到新卡片。
5. 重新打开详情，核对 provider 名称、描述、Action 数量、`operationId`、每个参数的名称、类型、必填性和说明。
6. 报告状态时分别写明 `created`、`tested`、`bound_to_workflow`、`draft_saved`、`published` 和 `runtime_verified`；未执行的状态不能推断为成功。

## 大响应处理

Dify Cloud 的 Swagger API 工具与 HTTP Request 节点不是同一调用路径，但不能据此保证任意大响应都可用。创建一个返回完整大 JSON 的工具，不等于大响应已经通过运行验证。

- Dify Cloud 的服务端大小限制不可由工作区用户通过环境变量调整。
- 不要在后续 Code、Template 或 Variable Aggregator 节点中反复复制、拼装或重新输出完整大 JSON。
- 约 1 MB 仅作为大响应诊断的经验阈值，不是所有 Cloud 版本的固定限制；实际阈值以目标错误和服务约束为准。对大体积原始响应，优先让上游 API 或工具本身支持字段精简、筛页、分页或分块，并让每次返回显著低于平台限制。
- 若外部入参必须保持 URL，可在工作流内部提取资源 ID，再分页调用工具；外部入参和最终出参无需因此改变。
- 若只能使用原始大响应工具，先单独测试工具调用，再测试紧邻的解析/压缩节点；只有完整链路真实运行成功后才能标记 `runtime_verified=true`。

## 工作流绑定

- 工具节点的 provider ID、tool name、参数定义和凭据绑定必须从目标工作区当前工具详情、当前草稿或同租户导出中取得，不根据显示名称猜测。
- `operationId` 决定 Action 标识；provider 显示名称不是稳定 provider ID。
- 绑定前读取最新草稿并锁定当前 hash/指纹，只改工具节点及明确要求的连线和变量映射；保存后 GET 回读并核对保护字段未变化。
- 创建工具不会自动把它加入任何工作流；绑定草稿也不会自动发布或执行。
