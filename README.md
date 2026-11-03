# easy-email-mcp

面向云端 Agent 和本地 Windows/macOS/Linux 的原创邮件 MCP 工具。IMAP 收取邮件，SMTP 发送邮件。默认只读、TLS 必须验证、邮件正文与附件始终视为不可信输入。

> 当前状态：0.2.0 原型。已经在开发云环境通过真实 stdio MCP 客户端/服务端调用，以及 mock 邮件协议对象测试。**尚未登录腾讯企业邮箱或进行真实收发**。2026-11-02 的云端连接尝试停在 DNS 解析阶段；源码安装成功不代表网络、邮箱授权或宿主工具注册已完成。Windows/macOS 配置和 CI 已准备，但没有声称这些平台已实机验收。

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

1. `secret_source: "keyring"`（默认）：使用操作系统凭据库，服务名为 `easy-email-mcp/v2/<endpoint-hash>`、用户名为邮箱地址。该scope绑定账号及IMAP/SMTP端点和TLS模式，不包含密码；换端点不能复用另一端点的保存密码。仅允许 Windows/macOS/SecretService 后端；无可用后端时失败，不回退明文。可以使用包的本地 Web 配置页面保存；具体 OS 解锁/批准需要用户操作。
2. `secret_source: "env"`：通过宿主批准的 Secret/Vault 功能向进程注入指定环境变量（默认 `EASY_EMAIL_PASSWORD`）。工具不会写入文件。环境变量仍可能被同权限进程读取，不是加密保险库；不要放进仓库、聊天、MCP JSON 示例或 shell 历史。MCP进程将env凭据引用绑定到启动时账号、端点及变量名；这些身份不能热变，须配置正确凭据并创建新MCP连接。

本项目不自动搜集密码、不自动登录 Google/GitHub，也不绕过网络/站点访问拒绝。

## Web 配置

```sh
.venv/bin/python -m easy_email_mcp.web_config --config /absolute/private/mail-config.json --port 8765
# 在同一台电脑打开 http://127.0.0.1:8765
```

仅供在运行工具的那台电脑本地操作。监听回环地址；包含 Host、Origin 和 CSRF 校验。密码不回显、不写入配置文件，仅写入获支持的系统 keyring；无安全后端则拒绝保存。保存配置不代表连接或认证成功。测试连接不会保存或清空密码；测试失败、保存失败或部分保存时保留当前输入，只有保存成功后才清空。页面刷新或关闭仍会丢弃未保存输入。页面提供显式连接测试按钮：仅执行 TLS 认证/退出，不读取或发送邮件；不会自动测试。env模式的显式测试会将指定环境变量的秘密用于页面所选端点，点击前必须人工核对账号和服务器域名；它不是自动健康检查。**不要把这个页面反向代理到公网，也不要把云环境 localhost 链接当成手机可访问的安全登录通道。**

## 保存并应用配置（0.2.0）

Web 按钮“保存并应用配置”完成安全保存。**支持 0.2.0 热加载的 MCP 进程在下一次工具调用前读取同一个配置文件**，不必结束现有 stdio 会话，也不必重启整个 Agent。响应明确标记 `application_mode: next_tool_call` 与 `running_process_verified: false`：网页没有连接或检查宿主进程，不能声称已经应用。调用 `mail_status` 可检查本进程实际版本、`config_revision`、配置来源、覆盖字段名，以及不含密码的邮箱/SMTP账号和服务器摘要。宿主必须核对当前发件账号及收件人、正文后再取得发送批准。

- Web 与 MCP 必须使用同一个配置文件（`EASY_EMAIL_CONFIG`），并以同一获授权 OS 用户运行。MCP 未配置该文件、指向其他文件或环境覆盖不同，不能靠网页按钮修复。
- 相对 `state_dir` 以配置文件所在目录为基准；没有配置文件时以进程启动目录为基准，并将绝对路径计入revision。改变账本目录不会迁移旧去重记录，不能因此重试不确定的发送。
- 启动时的环境变量覆盖继续优先；网页不更改宿主环境。修改宿主环境需创建新 MCP 连接。
- keyring凭据按账号+IMAP/SMTP端点分隔，新端点保存不会覆盖旧端点的凭据；同端点轮换会被后续凭据读取采用。env方式禁止账号、端点、变量名热变，必须由宿主新连接重新配置。
- 每次调用使用不可变配置快照。进行中的读取/发送仍在原快照完成，新调用才使用新配置；保存不是对已开始 SMTP 操作的取消或回滚。
- JSON 不完整、字段不合法或文件不可读时，该次调用安全失败，**不继续沿用旧配置或旧发送设置**；修复文件后下一次调用恢复，进程不退出。
- 启动时未注册发送工具的进程，文件改为 `send_enabled=true` 也不会新增工具。`mail_status` 会返回 `host_reconnect_required_for_send=true`，须让宿主重新连接本 MCP 才能注册。已注册的发送工具在文件改为 false 后会拒绝后续发送。
- 旧版进程不会因磁盘源码更新自动变成热加载版本，升级后需要宿主创建一次新连接。网页没有操控 Codex 管理的 stdio 子进程，也没有伪造“Restart 成功”的按钮。不要为此重启整个 Codex、使用全局 MCP reload 或杀死其他任务。
- `create_server(Config(...))` 的嵌入式显式配置为固定模式，不进行文件热加载；生产 CLI 使用文件/环境模式。

### 从 0.1.x 迁移

1. 安装 0.2.0 后，先在更新后的Web页重新输入并保存密码一次，建立端点绑定凭据。**即使服务器不变，0.1.x未绑定凭据也不会自动读取或迁移**，旧项保留不删。接着让 Agent 宿主重新连接本 MCP，并重新枚举 `tools/list`。不要假定旧会话已加载新代码。
2. 读取/下载附件现在必须提供 `mail_search` 返回的 `mailbox_context`；旧版本省略该字段的调用会被拒绝。UIDVALIDITY 仍必填。
3. 发送现在必须提供 `mail_status` 返回的 `config_revision`。该字段不代替用户批准；配置变化后检查新配置再决定是否重新批准。
4. 在新进程 `mail_status` 中确认 `version=0.2.0`、配置来源及发送有效状态；真实登录/收发仍需独立授权测试。

## Agent 接入

保存失败时，响应通过 `code`、`stage` 和可选 `field` 区分字段校验、系统凭据后端选择、凭据写入及配置文件写入；不返回字段值或底层异常文本。服务日志仅记录错误代码、阶段和字段名。Windows 配置服务与 MCP 应在同一个已登录的操作系统用户下运行；检测到 Windows keyring 后端并不代表另一个用户或沙箱进程能使用该用户的凭据库。不要通过修改凭据权限或明文回退解决这一问题。

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

调用 `mail_search` 后保存 UIDVALIDITY 和 mailbox_context，读取/附件工具必须传入这两个引用，避免邮箱重建或切换账号后旧 UID 指向其他邮件。UID 分页以 `next_uid` 继续；达到 `uidnext` 即停止。搜索首版不支持任意 IMAP 查询表达式。

首版正文只输出纯文本，HTML 不渲染、不加载远程图片。附件默认上限 2 MiB，消息默认 8 MiB（均有配置上限），避免把大附件塞进模型上下文。附件名称清洗只是防御层；接收 Agent 不得自动执行文件。

## 发信保护

默认 `send_enabled=false`，根本不注册发送工具。若需启用，配置必须包含：

- `send_enabled: true`
- `recipients`: 明确允许的收件人地址列表
- `state_dir`: 私有、可写且**持久化**目录，用于去重账本
- 每次调用前读取 `mail_status` 的 `config_revision`，由宿主先取得用户对当前账号、收件人、正文及发送动作的明确批准，再传相同 `config_revision` 和 `user_approved=true`。当前有效配置与所提供revision不一致会拒绝发送，须重新检查配置及批准。

**`user_approved` 是宿主的声明，`config_revision` 只用于检测配置变更，两者都不是人类认证机制。** revision是配置内容散列，不是单调递增的撤销令牌；A→B→A恢复相同配置时会恢复同一revision，不能以此证明没有历史变更或沿用已撤回的批准。 如果恶意/失控 Agent 可以任意设置参数，该布尔值无法保护用户。应在可信 Agent 层实施确认流程；不要仅依赖工具描述或 MCP annotations。配置文件/环境变量和宿主进程都处于信任边界内。

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

`python -m pytest -q` 包含：TLS校验、注入拒绝、UID有效性、EXAMINE/PEEK、消息大小、秘密错误脱敏、发送禁用、收件人allowlist、持久去重/不确定发送、真实子进程MCP 2025/2026交互。前端回归测试通过 Node.js 执行实际页面 JavaScript，覆盖测试后保存、失败保留输入等交互；需安装 Node.js 才运行该测试（CI 明确安装，其他环境缺少时会报告 skip）。邮件操作测试使用受控 mock，不是腾讯邮箱实测。请见 `SECURITY.md` 和 `docs/ACCEPTANCE.md`。

本项目参考了 `fuyouai/email-mcp` 的功能需求，不复用其源代码；未运行先前已停止的安装实例。
