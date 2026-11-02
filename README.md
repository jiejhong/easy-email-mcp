# easy-email-mcp

面向云端 Agent 和本地 Windows/macOS/Linux 的原创邮件 MCP 工具。IMAP 收取邮件，SMTP 发送邮件。默认只读、TLS 必须验证、邮件正文与附件始终视为不可信输入。

> 当前状态：0.1.0 原型。已经在开发云环境通过真实 stdio MCP 客户端/服务端调用，以及 mock 邮件协议对象测试。**尚未登录腾讯企业邮箱或进行真实收发**。2026-11-02 的云端连接尝试停在 DNS 解析阶段；源码安装成功不代表网络、邮箱授权或宿主工具注册已完成。Windows/macOS 配置和 CI 已准备，但没有声称这些平台已实机验收。

## 核心路径

`Agent → easy-email-call（官方 MCP Client）→ stdio → easy-email-mcp → TLS IMAP/SMTP`

- 云 Agent 有 shell/exec 权限时可直接运行 `easy-email-call`。这是一次**真正的 MCP 协议调用**，不是绕开 MCP 直接读邮件。
- 原生支持 MCP 的 Agent（例如本地编辑器）可直接启动 `easy-email-mcp`，无需桥接 CLI。
- 在某个云工作目录安装包，**不会自动注册成该云平台的原生工具**，也不保证跨会话、跨机器持久存在。宿主必须有受支持的 MCP 注册机制，或者获授权地使用上述 CLI 桥接；环境被回收后需重建。
- 不发布未鉴权的 HTTP MCP 端口。首版只提供 stdio；配置 Web 仅监听本机回环。

## 安装

要求 Python 3.11+。官方 MCP SDK 固定为 `mcp==2.3.0`，其余依赖范围见 `pyproject.toml`。

Linux/macOS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m easy_email_mcp.client mail_status
.venv/bin/python -m pytest -q
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m easy_email_mcp.client mail_status
.\.venv\Scripts\python.exe -m pytest -q
```

推荐使用模块启动形式，不把虚拟环境跨目录/跨 OS 复制。默认 `mail_status` 不访问网络、不读取密码。

## 配置文件

复制 `examples/config.example.json` 到私有配置目录，设置 `EASY_EMAIL_CONFIG` 指向它。环境变量覆盖配置文件。配置文件只含服务器和账户设置，**禁止 password/token 字段**。文件格式未知字段、错误类型和超限配置会被拒绝。

```sh
export EASY_EMAIL_CONFIG=/absolute/private/mail-config.json
.venv/bin/python -m easy_email_mcp.client mail_status
```

腾讯企业邮箱默认服务器为 `imap.exmail.qq.com:993` 和 `smtp.exmail.qq.com:465`。也可以配置其他标准 IMAP TLS 及 SMTP TLS/STARTTLS 服务器。该默认设置不是腾讯兼容认证，实际账号可能需要管理员开启协议或客户端专用授权码。

密码支持两条**显式**路径：

1. `secret_source: "keyring"`（默认）：使用操作系统凭据库，服务名 `easy-email-mcp`、用户名为邮箱地址。仅允许 Windows/macOS/SecretService 后端；无可用后端时失败，不回退明文。可以使用包的本地 Web 配置页面保存；具体 OS 解锁/批准需要用户操作。
2. `secret_source: "env"`：通过宿主批准的 Secret/Vault 功能向进程注入指定环境变量（默认 `EASY_EMAIL_PASSWORD`）。工具不会写入文件。环境变量仍可能被同权限进程读取，不是加密保险库；不要放进仓库、聊天、MCP JSON 示例或 shell 历史。

本项目不自动搜集密码、不自动登录 Google/GitHub，也不绕过网络/站点访问拒绝。

## Web 配置

```sh
.venv/bin/python -m easy_email_mcp.web_config --config /absolute/private/mail-config.json --port 8765
# 在同一台电脑打开 http://127.0.0.1:8765
```

仅供在运行工具的那台电脑本地操作。监听回环地址；包含 Host、Origin 和 CSRF 校验。密码不回显、不写入配置文件，仅写入获支持的系统 keyring；无安全后端则拒绝保存。保存配置不代表连接或认证成功。页面提供显式连接测试按钮：仅执行 TLS 认证/退出，不读取或发送邮件；不会自动测试。**不要把这个页面反向代理到公网，也不要把云环境 localhost 链接当成手机可访问的安全登录通道。**

## Agent 接入

通用 stdio 配置见 `examples/mcp-config.example.json`。把 Python 解释器和配置文件路径替换成目标机器上的真实路径，凭据交给宿主的安全注入或 OS keyring。示例不包含任何真实账户或密码。

云端调用（参数走 stdin，避免邮件正文进入命令行历史）：

```sh
.venv/bin/python -m easy_email_mcp.client list
printf '%s' '{"folder":"INBOX","limit":10}' | .venv/bin/python -m easy_email_mcp.client mail_search --stdin
```

工具：

| 工具 | 用途 |
|---|---|
| `mail_status` | 本地配置状态，不连接网络，不透露密码 |
| `mail_list_folders` | 返回原始 IMAP LIST（非 ASCII 文件夹使用服务器 modified UTF-7 名称） |
| `mail_search` | 按 UID 分页查询，可筛选未读，每次最多扫描 1,000 个 UID 区间 |
| `mail_read` | 通过 UID+UIDVALIDITY 读取正文和附件元数据，EXAMINE + BODY.PEEK，不标记已读 |
| `mail_attachment` | 返回受限附件 base64 和 SHA256，不落盘、不执行 |
| `mail_send` | 仅显式开启时注册，发送纯文本邮件；不提供删除/移动工具 |

调用 `mail_search` 后保存 UIDVALIDITY，读取时必须传入，避免邮箱重建后 UID 指向错误邮件。UID 分页以 `next_uid` 继续；达到 `uidnext` 即停止。搜索首版不支持任意 IMAP 查询表达式。

首版正文只输出纯文本，HTML 不渲染、不加载远程图片。附件默认上限 2 MiB，消息默认 8 MiB（均有配置上限），避免把大附件塞进模型上下文。附件名称清洗只是防御层；接收 Agent 不得自动执行文件。

## 发信保护

默认 `send_enabled=false`，根本不注册发送工具。若需启用，配置必须包含：

- `send_enabled: true`
- `recipients`: 明确允许的收件人地址列表
- `state_dir`: 私有、可写且**持久化**目录，用于去重账本
- 每次调用由宿主先取得用户对收件人、正文及发送动作的明确批准，再传 `user_approved=true`

**`user_approved` 是宿主的声明，不是人类认证机制。** 如果恶意/失控 Agent 可以任意设置参数，该布尔值无法保护用户。应在可信 Agent 层实施确认流程；不要仅依赖工具描述或 MCP annotations。配置文件/环境变量和宿主进程都处于信任边界内。

每次发信使用唯一 `request_id`，服务先持久化 `uncertain`，然后才连接 SMTP。成功只表示 `accepted_by_smtp`，不证明送达。相同 ID 不会重发；超时/断线时不自动重试，必须先人工或授权流程核实实际发出状态。不要通过换 ID 绕过去重。SMTP不提供跨服务端 exactly-once 保证。账本只存请求 ID、邮件内容散列和状态，不存正文、密码或完整收件人。

不支持上传任意本地路径作为发信附件，避免 Agent 越权读取主机文件。首版只发送纯文本，后续可添加严格授权的附件入口。

## MCP 2025 / 2026 兼容

使用官方 Python SDK 2.3.0，不手写协议栈。

- `2025-03-26`、`2025-06-18`、`2025-11-25`：legacy `initialize` 握手；测试逐个版本的实际 stdio 初始化和工具调用。
- `2026-07-28`：modern `server/discover` 和逐请求协议 metadata；真实官方 Client 自动发现及工具调用测试。
- `easy-email-call --mode legacy`：强制握手时代。
- `easy-email-call --mode auto`：默认发现现代协议，必要时回退。

“2026 兼容”不等于任意未来日期版本兼容；没有测试的扩展、SSE/HTTP 和第三方宿主不作保证。

官方依据：
- https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://py.sdk.modelcontextprotocol.io/protocol-versions/

## Docker

```sh
docker build -t easy-email-mcp .
# 仅验证无凭据状态：
docker run --rm -i easy-email-mcp
```

容器默认以非 root 用户运行 stdio MCP。接入时需要通过宿主安全机制提供配置、秘密和持久化 state 目录；Docker 容器通常没有可用 OS keyring，不能自动回退。Docker 构建尚需在目标环境验证。

## 测试与局限

`python -m pytest -q` 包含：TLS校验、注入拒绝、UID有效性、EXAMINE/PEEK、消息大小、秘密错误脱敏、发送禁用、收件人allowlist、持久去重/不确定发送、真实子进程MCP 2025/2026交互。邮件操作测试使用受控 mock，不是腾讯邮箱实测。请见 `SECURITY.md` 和 `docs/ACCEPTANCE.md`。

本项目参考了 `fuyouai/email-mcp` 的功能需求，不复用其源代码；未运行先前已停止的安装实例。
