"""Synthetic attachment tests; all SMTP is mocked, including child processes."""
import asyncio
import base64
from dataclasses import replace
from email import policy
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest

from easy_email_mcp.attachments import SendAttachment, prepare_attachments
from easy_email_mcp.client import read_arguments
from easy_email_mcp.config import Config, MailError
from easy_email_mcp.mail import MailService
from easy_email_mcp.server import create_server


def attachment(data=b"\x00\x01\xfffixture", filename="报告.bin", content_type="application/octet-stream"):
    return {"filename": filename, "base64": base64.b64encode(data).decode("ascii"), "content_type": content_type}


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_EMAIL_PASSWORD", "fixture-only")
    return Config(username="sender@example.invalid", secret_source="env", send_enabled=True,
                  recipients=("reader@example.invalid",), state_dir=str(tmp_path / "ledger"))


@pytest.fixture
def smtp(monkeypatch):
    sent, events = [], []
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            if "context" in kwargs:
                assert kwargs["context"].check_hostname
            events.append("connect")
        def ehlo(self): events.append("ehlo")
        def starttls(self, *, context):
            assert context.check_hostname
            events.append("starttls")
        def login(self, *args): events.append("login")
        def send_message(self, message):
            sent.append(message)
            events.append("send")
            return {}
        def quit(self): events.append("quit")
    monkeypatch.setattr("easy_email_mcp.mail.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("easy_email_mcp.mail.smtplib.SMTP", FakeSMTP)
    return sent, events


def send(config, attachments=None, request_id="attachment_req_001", **kwargs):
    return MailService(config).send(["reader@example.invalid"], "附件测试", "正文 body", request_id,
                                    True, attachments=attachments, **kwargs)


@pytest.mark.parametrize("security", ["tls", "starttls"])
def test_binary_unicode_multiple_and_empty_files(enabled, smtp, security):
    files = [attachment(), attachment("你好\n".encode(), "说明.txt", "text/plain"), attachment(b"", "empty.dat")]
    result = send(replace(enabled, smtp_security=security), files)
    assert result["status"] == "accepted_by_smtp" and result["delivery_confirmed"] is False
    sent, events = smtp
    assert len(sent) == 1
    parsed = BytesParser(policy=policy.default).parsebytes(sent[0].as_bytes())
    assert parsed.get_content_type() == "multipart/mixed"
    assert parsed.get_body().get_content().strip() == "正文 body"
    for part, original, meta in zip(parsed.iter_attachments(), files, result["attachments"], strict=True):
        raw = base64.b64decode(original["base64"])
        assert part.get_payload(decode=True) == raw
        assert part.get_filename() == original["filename"]
        assert part.get_content_disposition() == "attachment"
        assert part.get_content_type() == original["content_type"]
        assert meta == {"filename": original["filename"], "content_type": original["content_type"],
                        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if security == "starttls":
        assert events == ["connect", "ehlo", "starttls", "ehlo", "login", "send", "quit"]


@pytest.mark.parametrize("files", [
    "not-a-list", {}, [attachment()] * 11, [None], [{"filename": "x"}],
    [{**attachment(), "path": "/private/secret"}], [{**attachment(), "url": "https://example.invalid/x"}],
    [attachment(filename="../secret")], [attachment(filename="C:\\secret")],
    [attachment(filename="bad\r\nBcc: injected")], [attachment(filename="NUL")],
    [attachment(filename="")], [attachment(filename="x" * 121)],
    [attachment(content_type="text/plain; charset=utf-8")],
    [attachment(content_type="text/plain\r\nX-Header: injected")],
    [attachment(content_type="message/rfc822")], [attachment(content_type="multipart/mixed")],
    [{**attachment(), "base64": "***"}], [{**attachment(), "base64": "Zg==\n"}],
    [{**attachment(), "base64": "Zg"}], [{**attachment(), "base64": "Zh=="}],
    [{**attachment(), "base64": "é"}], [{**attachment(), "base64": 123}],
    [{**attachment(), "filename": 123}], [{**attachment(), "content_type": None}],
])
def test_invalid_rejected_before_secret_ledger_or_network(enabled, files):
    with patch.object(Config, "password", side_effect=AssertionError("must not retrieve secret")), \
         patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        with pytest.raises(MailError):
            send(enabled, files)
    assert not Path(enabled.state_dir).exists()


def test_size_limits_before_decode_and_mime_overhead(enabled, smtp):
    config = replace(enabled, max_attachment_bytes=3)
    with patch("easy_email_mcp.attachments.base64.b64decode", side_effect=AssertionError("must not decode oversized input")):
        with pytest.raises(MailError, match="limit"):
            send(config, [attachment(b"1234")])
    assert send(config, [attachment(b"123")])["status"] == "accepted_by_smtp"
    with patch.object(Config, "password", side_effect=AssertionError("must not retrieve secret")):
        with pytest.raises(MailError, match="Encoded email"):
            send(replace(enabled, max_message_bytes=600), [attachment(b"x" * 500)], "mime_limit_req_01")
        with pytest.raises(MailError, match="combined"):
            send(replace(enabled, max_message_bytes=6), [attachment(b"1234"), attachment(b"5678")])
    assert len(smtp[0]) == 1


def test_maximum_file_and_count_boundaries(enabled):
    config = replace(enabled, max_attachment_bytes=4 * 1024 * 1024)
    file = attachment(b"x" * config.max_attachment_bytes)
    assert len(prepare_attachments([file], config)[0].data) == config.max_attachment_bytes
    assert len(prepare_attachments([attachment(b"")] * 10, config)) == 10
    with pytest.raises(MailError):
        prepare_attachments([attachment(b"x" * (config.max_attachment_bytes + 1))], config)


def test_exact_retry_and_changed_bytes_name_type_or_order(enabled, smtp):
    original = [attachment(b"one"), attachment(b"two", "second.dat")]
    first = send(enabled, original)
    again = send(enabled, original)
    assert again["retried"] is False and again["attachments"] == first["attachments"]
    variants = [[attachment(b"changed"), original[1]], [{**original[0], "filename": "new.bin"}, original[1]],
                [{**original[0], "content_type": "image/png"}, original[1]], original[::-1], original[:1], [], None]
    for files in variants:
        with pytest.raises(MailError, match="different content"):
            send(enabled, files)
    assert len(smtp[0]) == 1
    with sqlite3.connect(enabled.state_dir + "/send-ledger.sqlite3") as db:
        record = db.execute("SELECT * FROM sends").fetchone()
    assert len(record[1]) == 64
    assert "one" not in str(record) and "报告" not in str(record)


def test_legacy_plain_text_digest_is_unchanged(enabled, smtp):
    Path(enabled.state_dir).mkdir()
    digest = hashlib.sha256(json.dumps([enabled.username, ["reader@example.invalid"], "附件测试", "正文 body"], ensure_ascii=False).encode()).hexdigest()
    with sqlite3.connect(enabled.state_dir + "/send-ledger.sqlite3") as db:
        db.execute("CREATE TABLE sends (id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL)")
        db.execute("INSERT INTO sends VALUES (?,?,?)", ("attachment_req_001", digest, "uncertain"))
    assert send(enabled)["status"] == "uncertain"
    assert send(enabled, [])["retried"] is False
    assert smtp[0] == []


def test_uncertain_attachments_never_retry(enabled, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("private error")
    monkeypatch.setattr("easy_email_mcp.mail.smtplib.SMTP_SSL", fail)
    result = send(enabled, [attachment()])
    assert result["status"] == "uncertain" and result["retry_safe"] is False
    assert "private error" not in str(result)
    assert send(enabled, [attachment()])["retried"] is False
    assert calls == [1]


def test_attachment_sending_still_requires_all_protections(enabled, smtp):
    configs = [replace(enabled, send_enabled=False), replace(enabled, recipients=()), replace(enabled, state_dir="")]
    with patch.object(Config, "password", side_effect=AssertionError("must not retrieve secret")):
        for config in configs:
            with pytest.raises(MailError):
                send(config, [attachment()])
        with pytest.raises(MailError):
            MailService(enabled).send(["reader@example.invalid"], "s", "b", "attachment_req_001", attachments=[attachment()])
    assert not smtp[0]


def test_official_mcp_client_schema_validation_and_large_attachment(enabled, smtp):
    async def run():
        async with Client(create_server(enabled), read_timeout_seconds=30) as client:
            tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "mail_send")
            schema = tool.model_dump(mode="json", by_alias=True)["inputSchema"]
            assert "attachments" in schema["properties"]
            assert "attachments" not in schema.get("required", [])
            status = json.loads((await client.call_tool("mail_status", {})).content[0].text)
            assert status["send_attachment_limits"]["max_count"] == 10
            files = [attachment(b"x" * (1024 * 1024 + 1))]
            arguments = {"to": ["reader@example.invalid"], "subject": "MCP attachment", "body": "fixture",
                         "request_id": "mcp_attachment_001", "user_approved": True,
                         "config_revision": status["config_revision"], "attachments": files}
            with patch.object(Config, "password", side_effect=AssertionError("must not retrieve secret")):
                assert (await client.call_tool("mail_send", {**arguments, "config_revision": "stale"})).is_error
                assert (await client.call_tool("mail_send", {**arguments, "attachments": [{**attachment(), "path": "forbidden"}]})).is_error
            result = await client.call_tool("mail_send", arguments)
            assert not result.is_error
            data = json.loads(result.content[0].text)
            assert data["status"] == "accepted_by_smtp"
            assert data["attachments"][0]["bytes"] == 1024 * 1024 + 1
            assert len(smtp[0]) == 1
    asyncio.run(asyncio.wait_for(run(), timeout=90))


def test_cli_binary_input_supports_large_attachment_and_has_finite_cap(monkeypatch):
    arguments = {"attachments": [attachment(b"x" * 1024 * 1024)]}
    payload = json.dumps(arguments).encode()
    assert len(payload) > 1024 * 1024
    assert read_arguments(BytesIO(payload)) == arguments
    monkeypatch.setattr("easy_email_mcp.client.MAX_SEND_ARGUMENT_BYTES", 2)
    assert read_arguments(BytesIO(b"{}")) == {}
    with pytest.raises(ValueError, match="large"):
        read_arguments(BytesIO(b"{} "))
    with pytest.raises(ValueError, match="object"):
        read_arguments(BytesIO(b"[]"))


def test_typed_attachment_and_default_content_type(enabled):
    typed = SendAttachment(filename="file.bin", base64="AA==")
    assert prepare_attachments([typed], enabled)[0].data == b"\x00"
    assert prepare_attachments([{"filename": "file.bin", "base64": ""}], enabled)[0].content_type == "application/octet-stream"


@pytest.mark.parametrize("mode,version", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_large_attachment_over_real_stdio(tmp_path, mode, version):
    # Patch inside the subprocess: no real SMTP or credential retrieval is possible.
    script = r'''
import hashlib
import socket
import sys
from pathlib import Path
from email import policy
from email.parser import BytesParser
from easy_email_mcp.config import Config
from easy_email_mcp.server import create_server
import easy_email_mcp.mail as mail

def forbidden(*args, **kwargs):
    raise AssertionError("Real network access forbidden")
socket.create_connection = forbidden
socket.getfqdn = forbidden
Config.password = lambda self: "synthetic-only"
class FakeSMTP:
    def __init__(self, *args, **kwargs):
        assert kwargs["context"].check_hostname
    def login(self, username, password):
        assert password == "synthetic-only"
    def send_message(self, message):
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        parts = list(parsed.iter_attachments())
        assert len(parts) == 1
        raw = parts[0].get_payload(decode=True)
        assert raw == b"x" * (1024 * 1024 + 1)
        assert parts[0].get_filename() == "报告.bin"
        with Path(sys.argv[2]).open("a") as output:
            output.write(hashlib.sha256(raw).hexdigest() + "\n")
        return {}
    def quit(self): pass
mail.smtplib.SMTP_SSL = FakeSMTP
mail.smtplib.SMTP = forbidden
config = Config(username="sender@example.invalid", secret_source="env", send_enabled=True,
                recipients=("reader@example.invalid",), state_dir=sys.argv[1])
create_server(config).run()
'''
    async def run():
        env = {k: v for k, v in os.environ.items() if not k.startswith("EASY_EMAIL_")}
        marker = tmp_path / "smtp-calls.txt"
        params = StdioServerParameters(command=sys.executable,
                    args=["-c", script, str(tmp_path / "ledger"), str(marker)], env=env)
        async with Client(params, mode=mode, read_timeout_seconds=30) as client:
            assert client.protocol_version == version
            status = json.loads((await client.call_tool("mail_status", {})).content[0].text)
            raw = b"x" * (1024 * 1024 + 1)
            args = {"to": ["reader@example.invalid"], "subject": "stdio attachment", "body": "fixture",
                    "request_id": "stdio_attachment_001", "user_approved": True,
                    "config_revision": status["config_revision"], "attachments": [attachment(raw)]}
            assert len(json.dumps(args).encode()) > 1024 * 1024
            result = await client.call_tool("mail_send", args)
            assert not result.is_error
            data = json.loads(result.content[0].text)
            assert data["status"] == "accepted_by_smtp"
            assert data["attachments"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert data["attachments"][0]["bytes"] == len(raw)
            again = await client.call_tool("mail_send", args)
            assert not again.is_error
            assert json.loads(again.content[0].text)["retried"] is False
        assert marker.read_text().splitlines() == [hashlib.sha256(raw).hexdigest()]
    asyncio.run(asyncio.wait_for(run(), timeout=90))


def test_message_id_uses_sender_domain_without_hostname_dns(enabled, smtp):
    with patch("socket.getfqdn", side_effect=AssertionError("hostname DNS forbidden")):
        assert send(enabled, [attachment()])["status"] == "accepted_by_smtp"
        assert send(enabled, [attachment()])["retried"] is False
    assert len(smtp[0]) == 1
    assert str(smtp[0][0]["Message-ID"]).endswith("@example.invalid>")
