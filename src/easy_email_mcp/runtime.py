"""Per-call immutable configuration snapshots; never restart a host-owned process."""
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import threading

from .config import Config, MailError, keyring_service
from .mail import MailService

# Configuration references only. In particular, do not snapshot password values.
ENV_FIELDS = {
    "IMAP_HOST": "imap_host", "IMAP_PORT": "imap_port",
    "SMTP_HOST": "smtp_host", "SMTP_PORT": "smtp_port",
    "SMTP_SECURITY": "smtp_security", "USERNAME": "username",
    "SECRET_SOURCE": "secret_source", "SECRET_ENV": "secret_env",
    "SEND_ENABLED": "send_enabled", "ALLOWED_RECIPIENTS": "recipients",
    "STATE_DIR": "state_dir",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mailbox_context(config):
    # No password, credential-store content or environment secret value is read.
    return fingerprint(["mailbox-v1", config.imap_host, config.imap_port, config.username])


@dataclass(frozen=True)
class Snapshot:
    config: Config
    service: MailService
    revision: str
    mailbox_context: str
    requested_send_enabled: bool


class ConfigRuntime:
    def __init__(self, config=None):
        self.lock = threading.Lock()
        self.fixed = Config.from_mapping(vars(config)) if config is not None else None
        names = ["EASY_EMAIL_CONFIG", *("EASY_EMAIL_" + key for key in ENV_FIELDS)]
        self.env = {key: os.environ[key] for key in names if key in os.environ}
        if self.env.get("EASY_EMAIL_CONFIG"):
            self.env["EASY_EMAIL_CONFIG"] = str(Path(self.env["EASY_EMAIL_CONFIG"]).expanduser().absolute())
        self.base_directory = Path(self.env["EASY_EMAIL_CONFIG"]).parent if self.env.get("EASY_EMAIL_CONFIG") else Path.cwd()
        initial = self.fixed if self.fixed is not None else Config.from_env(self.env)
        self.initial = initial
        self.send_tool_registered = initial.send_enabled
        self.current = self._make_snapshot(initial)

    def _normalized(self, config):
        # A relative ledger path is anchored to the config file, not the caller's
        # changing CWD. Without a file, anchor it to the startup directory.
        if config.state_dir:
            path = Path(config.state_dir).expanduser()
            if not path.is_absolute():
                path = self.base_directory / path
            config = replace(config, state_dir=os.path.abspath(path))
        return config

    def _make_snapshot(self, requested):
        requested = self._normalized(requested)
        # A file edit may turn sends off, but never add a send tool to a server
        # which started read-only. Capability expansion requires host reconnect.
        effective = replace(requested, send_enabled=requested.send_enabled and self.send_tool_registered)
        return Snapshot(effective, MailService(effective),
                        fingerprint(["configuration-v1", vars(effective), requested.send_enabled]),
                        mailbox_context(effective), requested.send_enabled)

    def snapshot(self):
        with self.lock:
            # Do not silently reuse stale credentials/configuration after a bad
            # write. Raising affects only this call; the MCP process stays alive.
            requested = self.fixed if self.fixed is not None else Config.from_env(self.env)
            if requested.secret_source == "env" and (
                self.initial.secret_source != "env" or keyring_service(requested) != keyring_service(self.initial)
                or requested.secret_env != self.initial.secret_env
            ):
                raise MailError("Environment credentials are pinned to the startup account, mail endpoints and secret variable. Configure the intended credential and reconnect this MCP before changing that identity.")
            requested = self._normalized(requested)
            effective = replace(requested, send_enabled=requested.send_enabled and self.send_tool_registered)
            if effective != self.current.config or requested.send_enabled != self.current.requested_send_enabled:
                self.current = self._make_snapshot(requested)
            return self.current

    def checked_mailbox(self, supplied_context):
        snapshot = self.snapshot()
        if supplied_context != snapshot.mailbox_context:
            raise MailError("Mailbox configuration changed or context is invalid; repeat mail_search before reading")
        return snapshot

    def status(self):
        snapshot = self.snapshot()
        return {
            "configured": bool(snapshot.config.username),
            "send_enabled": snapshot.config.send_enabled,
            "requested_send_enabled": snapshot.requested_send_enabled,
            "send_tool_registered": self.send_tool_registered,
            "host_reconnect_required_for_send": snapshot.requested_send_enabled and not self.send_tool_registered,
            "secret_source": snapshot.config.secret_source,
            "credential_scope": "endpoint_bound_v2" if snapshot.config.secret_source == "keyring" else "startup_identity_bound_env",
            "tls_required": True,
            "mailbox_connectivity_verified": False,
            "version": "0.2.0",
            "config_revision": snapshot.revision,
            "mailbox_identity": {"username": snapshot.config.username, "host": snapshot.config.imap_host, "port": snapshot.config.imap_port},
            "smtp_identity": {"username": snapshot.config.username, "host": snapshot.config.smtp_host, "port": snapshot.config.smtp_port, "security": snapshot.config.smtp_security},
            "configuration_reload": "before_each_tool_call" if self.fixed is None else "fixed_explicit_config",
            "configuration_source": "explicit" if self.fixed is not None else ("file_and_startup_environment" if self.env.get("EASY_EMAIL_CONFIG") else "startup_environment"),
            "environment_override_fields": [] if self.fixed is not None else sorted(ENV_FIELDS[key] for key in ENV_FIELDS if "EASY_EMAIL_" + key in self.env),
        }
