from dataclasses import dataclass
import os
import json
from pathlib import Path
import ssl


class MailError(Exception):
    """A safe, user-facing error with no server response or secrets."""


@dataclass(frozen=True)
class Config:
    imap_host: str = "imap.exmail.qq.com"
    imap_port: int = 993
    smtp_host: str = "smtp.exmail.qq.com"
    smtp_port: int = 465
    smtp_security: str = "tls"
    username: str = ""
    secret_source: str = "keyring"
    secret_env: str = "EASY_EMAIL_PASSWORD"
    send_enabled: bool = False
    recipients: tuple[str, ...] = ()
    max_message_bytes: int = 8 * 1024 * 1024
    max_attachment_bytes: int = 2 * 1024 * 1024
    timeout: int = 20
    state_dir: str = ""

    @classmethod
    def from_env(cls):
        values = {}
        config_path = os.getenv("EASY_EMAIL_CONFIG")
        if config_path:
            try:
                with Path(config_path).open("rb") as handle:
                    raw = handle.read(65537)
                if len(raw) > 65536:
                    raise ValueError()
                values = json.loads(raw)
            except Exception:
                raise MailError("Cannot read configuration JSON (max 64 KiB)") from None
            if not isinstance(values, dict):
                raise MailError("Configuration must be a JSON object")
            if set(values) - set(cls.__dataclass_fields__):
                raise MailError("Unknown configuration field; password values are forbidden")
        mapping = {
            "IMAP_HOST": "imap_host", "IMAP_PORT": "imap_port",
            "SMTP_HOST": "smtp_host", "SMTP_PORT": "smtp_port",
            "SMTP_SECURITY": "smtp_security", "USERNAME": "username",
            "SECRET_SOURCE": "secret_source", "SECRET_ENV": "secret_env",
            "SEND_ENABLED": "send_enabled", "ALLOWED_RECIPIENTS": "recipients",
            "STATE_DIR": "state_dir",
        }
        for suffix, field in mapping.items():
            name = "EASY_EMAIL_" + suffix
            if name in os.environ:
                value = os.environ[name]
                if field in {"imap_port", "smtp_port"}:
                    try:
                        value = int(value)
                    except ValueError:
                        raise MailError("Invalid port") from None
                elif field == "send_enabled":
                    if value.lower() not in {"true", "false"}:
                        raise MailError("SEND_ENABLED must be true or false")
                    value = value.lower() == "true"
                elif field == "recipients":
                    value = [x.strip().lower() for x in value.split(",") if x.strip()]
                values[field] = value
        return cls.from_mapping(values)

    @classmethod
    def from_mapping(cls, values):
        if not isinstance(values, dict) or set(values) - set(cls.__dataclass_fields__):
            raise MailError("Unknown configuration field; password values are forbidden")
        defaults = cls()
        for field, value in values.items():
            expected = getattr(defaults, field)
            if field == "recipients":
                if not isinstance(value, (tuple, list)) or any(not isinstance(v, str) for v in value):
                    raise MailError("recipients must be a list of addresses")
            elif type(value) is not type(expected):
                raise MailError("Invalid configuration field type")
        values = dict(values)
        if "recipients" in values:
            values["recipients"] = tuple(v.lower() for v in values["recipients"])
        cfg = cls(**values)
        if cfg.smtp_security not in {"tls", "starttls"}:
            raise MailError("SMTP must use tls or starttls; plaintext is forbidden")
        if cfg.secret_source not in {"keyring", "env"}:
            raise MailError("Secret source must be keyring or env")
        if not all(1 <= p <= 65535 for p in (cfg.imap_port, cfg.smtp_port)):
            raise MailError("Invalid server port")
        if not 1 <= cfg.timeout <= 60 or not 1 <= cfg.max_message_bytes <= 32 * 1024 * 1024 or not 1 <= cfg.max_attachment_bytes <= 4 * 1024 * 1024:
            raise MailError("Invalid timeout or byte limit")
        for host in (cfg.imap_host, cfg.smtp_host):
            if not host or len(host) > 253 or any(c.isspace() for c in host) or "/" in host or "@" in host:
                raise MailError("Invalid mail server hostname")
        return cfg

    def password(self):
        if not self.username:
            raise MailError("Mailbox not configured: set EASY_EMAIL_USERNAME")
        if self.secret_source == "env":
            value = os.getenv(self.secret_env)
        else:
            import keyring
            backend = keyring.get_keyring()
            name = backend.__class__.__module__
            if not name.startswith(("keyring.backends.SecretService", "keyring.backends.macOS", "keyring.backends.Windows")):
                raise MailError("No approved OS credential backend; no plaintext fallback")
            try:
                value = keyring.get_password("easy-email-mcp", self.username)
            except Exception:
                raise MailError("OS credential store unavailable") from None
        if not value:
            raise MailError("Mailbox secret is not available; use an approved secure credential channel")
        return value


def tls_context():
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
