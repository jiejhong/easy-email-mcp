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
- There is no remote HTTP MCP authentication surface in v0.2.0. An exec bridge is not automatic native MCP registration in a hosted Agent.

## Deliberately not performed in development

No real mailbox password was configured; no real login, mailbox read, send, delete, or network-policy bypass was performed. Mock tests and local stdio tests are not evidence of Tencent connectivity. GitHub publication and CI status must be separately verified.

## Configuration reload boundary (0.2.0)

Each call acquires an immutable validated snapshot before mail operations. Invalid or unreadable current configuration rejects that call, rather than falling back to the last valid secret reference or send policy. In-flight operations retain their original snapshot; configuration saving is not cancellation of an SMTP attempt. Startup environment overrides remain pinned.

The send tool is registered only if enabled when the process starts; file reload cannot expand this startup capability ceiling. A later disable blocks future sends. A mailbox identity fingerprint prevents reuse of UIDs after changing account/IMAP endpoint. A non-secret configuration revision prevents a send under stale configuration. Neither fingerprint nor revision proves human approval, and neither retrieves or hashes a password. The host still enforces approval and manages process reconnection. The configuration page does not control an existing host-owned stdio session.

Configuration revisions are deterministic content hashes, not monotonic revocation tokens: A→B→A restores the original revision. The host must not treat a matching hash as approval or proof that no intervening change occurred. mail_status exposes non-secret account/server identity for approval review. Relative send-ledger paths are anchored to the configuration file directory (or startup CWD without a file) and included as absolute paths in revisions. Changing the ledger directory does not migrate or restore deduplication history.

### Endpoint-bound credentials and migration

Version 0.2.0 stores OS-keyring credentials under `easy-email-mcp/v2/<hash>` where the hash covers account and both IMAP/SMTP endpoint identities, not secret material. There is no legacy-key fallback. Users must re-enter and save once through the updated UI, even for unchanged endpoints; old entries are retained and not silently read, copied or deleted. An in-flight old-endpoint configuration snapshot cannot read a new-endpoint password saved under the new scope. Same-endpoint password rotation may be observed by a later credential read, but does not cross endpoint identities.

For environment-secret mode the runtime rejects account, endpoint, variable-name or keyring-to-env identity changes after startup. Create a new MCP connection with an explicitly appropriate host-managed secret for a new identity. This does not protect against a malicious same-process host rewriting its own environment; that host is trusted.

The Web authentication test is an explicit user action, separate from MCP reload. In env mode it uses the selected environment-secret reference against the endpoints shown on the page; the trusted user must verify those endpoints before testing. Do not run it as an automatic health check.
