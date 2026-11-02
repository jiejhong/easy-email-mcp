# Security model

This is an initial, not independently audited implementation. Report suspected vulnerabilities privately to the repository owner; never attach actual mailbox credentials or private messages to a public issue.

## Trust boundaries

- The local OS user, MCP host, configuration file, injected environment and OS keyring are trusted. This server is not a sandbox against a malicious same-user process.
- Mail servers, message headers, bodies and attachments are untrusted. No HTML renderer, URL fetch, shell execution, archive extraction, filesystem attachment write or IMAP mutation tool is provided.
- Passwords come from an explicitly approved OS keyring backend or explicit environment injection. No cleartext config, automatic fallback or password logging is permitted. Environment injection is a convenience, not an encrypted vault.
- Local configuration UI is loopback-only with CSRF/Origin/Host checks. It is not an Internet-facing secret portal, and must not be reverse-proxied.
- MCP tool annotations and `user_approved` are descriptive/host-asserted, not cryptographic authorization. The trusted host must obtain real user approval before a send. Sending is disabled unless explicitly configured, with a recipient allowlist.

## Protections and residual risks

- Certificate/hostname verification and TLS >=1.2 are mandatory. SMTP supports implicit TLS or STARTTLS, not plaintext credential fallback.
- IMAP uses EXAMINE and BODY.PEEK. UIDVALIDITY is required so a recycled UID cannot silently select another message.
- IMAP literal size is checked before allocation; cumulative line/literal bytes have a per-connection budget. Each line also has Python imaplib's cap. Response timeouts cap blocking socket operations, not the full end-to-end wall clock of a malicious slow server.
- The bounded parser still parses MIME in memory. Resource limits reduce risk but cannot eliminate parser vulnerabilities; keep Python patched. Search uses bounded UID windows; very sparse mailboxes may require many calls.
- Returned attachment filenames are sanitized (including Windows reserved names), but consumers must independently constrain output directories and never execute attachments automatically.
- SMTP deduplication is persistent, best-effort at-most-once attempt per request ID. A failed attempt may not have sent anything and still remains uncertain. Never automatically retry with a new ID. Losing/restoring the state directory can lose deduplication guarantees. SMTP acceptance does not prove delivery.
- The send ledger stores request ID, SHA256 of content and result status, not raw mail. Restrict its directory to the owner using OS permissions; the application does not silently weaken existing permissions.
- There is no remote HTTP MCP authentication surface in v0.1.0. An exec bridge is not automatic native MCP registration in a hosted Agent.

## Deliberately not performed in development

No real mailbox password was configured; no real login, mailbox read, send, delete, or network-policy bypass was performed. Mock tests and local stdio tests are not evidence of Tencent connectivity. GitHub publication and CI status must be separately verified.
