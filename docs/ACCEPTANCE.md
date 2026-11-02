# 验收边界

## 已执行（当前 Linux 云工作区，Python 3.12.14）

- 官方 MCP SDK 2.3.0 安装、包 editable 安装、`pip check`。
- 官方 Client → 实际 subprocess stdio Server → `mail_status`/工具枚举。
- 2026-07-28 auto/explicit 客户端调用。
- 2025-03-26、2025-06-18、2025-11-25 原始 stdio `initialize` → `notifications/initialized` → 工具调用；测试逐步等待响应，不依赖管道提前EOF。
- 官方 MCP Client → in-process Server → 受控 IMAP mock → `mail_read` 返回内容。
- TLS/只读/UIDVALIDITY/预算/注入/秘密脱敏/SMTP去重/配置Web CSRF等自动测试。

邮件端使用 mock 对象。它验证调用顺序及保护，不代表真实网络服务实现互操作。

## 未完成，不可宣称成功

- 腾讯企业邮箱真实 DNS/TCP/TLS/账号认证、读取和发送：当前云DNS解析失败，无账号秘密配置。
- 在云平台原生 tool registry 注册：CLI桥接可用，不代表原生tool自动出现。
- 云端安全凭据交付：OS keyring支持代码已实现；本环境可用的安全交付渠道和后端仍待验证。不能在聊天里索取密码。
- Windows/macOS 实机、Docker构建、GitHub CI：有配置，不代表实际跑过。
- 真实邮箱发送验收必须用户明确指定测试收件人及内容，先校验发送开关、allowlist、持久state目录，观察SMTP结果；任何uncertain不得自动重发。
- Web视觉浏览器验收：路由/HTML/安全和mock测试已验证，但本次云浏览器与执行器loopback不互通，没有宣称完成浏览器交互截图验收。

## 上线前最小顺序

1. 通过获准网络路由解决DNS；不切换IP或域名绕过拒绝。
2. 在实际运行机器通过已批准的安全机制配置秘密，明确持久保存许可。
3. 显式执行Web连接测试（仅认证、不读/发信）。
4. 由Agent经MCP执行只读文件夹和一封指定邮件查询，检查已读标记未改变。
5. 用户明确批准测试邮件之后再开启受限发信，验证accepted与实际收件分别成立。
6. 逐个Agent/OS执行同一验收，记录SDK、协议日期、账号权限和结果，不以开发机通过替代。
