import json
import os
import re
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from easy_email_mcp.web_config import create_app


def setup(tmp_path):
    path = tmp_path / "config.json"
    client = TestClient(create_app(path), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1234))
    html = client.get("/").text
    token = re.search("const token='([^']+)'", html).group(1)
    headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": token}
    return client, path, headers


def test_save_config_no_secret_and_no_network(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        response = client.post("/api/config", json={"username": "user@example.com"}, headers=headers)
    assert response.status_code == 200
    assert "password" not in path.read_text()
    assert json.loads(path.read_text())["send_enabled"] is False
    assert client.get("/api/config").headers["cache-control"] == "no-store"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_origin_csrf_host_guard(tmp_path):
    client, path, headers = setup(tmp_path)
    assert client.post("/api/config", json={"username": "x"}).status_code == 403
    assert client.post("/api/config", json={"username": "x"}, headers={**headers,"Origin":"https://evil.example"}).status_code == 403
    assert client.get("/", headers={"Host":"evil.example:8765"}).status_code == 403
    remote = TestClient(create_app(path), base_url="http://127.0.0.1:8765", client=("10.0.0.4", 1))
    assert remote.get("/").status_code == 403
    assert not path.exists()


def test_password_fail_closed_and_not_echoed(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch("keyring.get_keyring", return_value=object()), patch("keyring.set_password") as save:
        result = client.post("/api/config", json={"username":"user@example.com","password":"FAKE_TEST_ONLY"}, headers=headers)
    assert result.status_code == 400
    assert "FAKE_TEST_ONLY" not in result.text
    assert not path.exists()
    save.assert_not_called()


def test_safe_keyring_password_never_in_config(tmp_path):
    client, path, headers = setup(tmp_path)
    backend = type("TestBackend", (), {"__module__":"keyring.backends.SecretService"})()
    with patch("keyring.get_keyring", return_value=backend), patch("keyring.set_password") as save:
        result = client.post("/api/config", json={"username":"user@example.com","password":"FAKE_TEST_ONLY"}, headers=headers)
    assert result.status_code == 200
    save.assert_called_once_with("easy-email-mcp", "user@example.com", "FAKE_TEST_ONLY")
    assert "FAKE_TEST_ONLY" not in path.read_text() + result.text + client.get("/api/config").text


def test_limits_and_secret_field_refusal(tmp_path):
    client, path, headers = setup(tmp_path)
    assert client.post("/api/config", json={"username":"x","password":"a"*20000}, headers=headers).status_code == 413
    assert client.post("/api/config", json={"username":"x","smtp_security":"none"}, headers=headers).status_code == 400
    assert client.post("/api/config", json={"username":"x","imap_port":True}, headers=headers).status_code == 400
    assert client.post("/api/config", json={"username":"x","secret_source":"env","password":"FAKE"}, headers=headers).status_code == 400
    path.write_text('{"password":"DO_NOT_ECHO"}')
    response = client.get("/api/config")
    assert response.status_code == 400 and "DO_NOT_ECHO" not in response.text


def test_explicit_connection_test_has_no_mail_operations_or_save(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch("easy_email_mcp.web_config.BoundedIMAP4SSL") as imap, patch("easy_email_mcp.web_config.smtplib.SMTP_SSL") as smtp:
        result = client.post("/api/test", json={"username":"user@example.com", "password":"FAKE_TEST_ONLY"}, headers=headers)
    assert result.json() == {"imap":"authenticated", "smtp":"authenticated"}
    imap.return_value.login.assert_called_once_with("user@example.com", "FAKE_TEST_ONLY")
    imap.return_value.select.assert_not_called()
    imap.return_value.logout.assert_called_once()
    smtp.return_value.login.assert_called_once()
    smtp.return_value.sendmail.assert_not_called()
    smtp.return_value.send_message.assert_not_called()
    smtp.return_value.quit.assert_called_once()
    assert not path.exists()
    assert imap.call_args.kwargs["ssl_context"].check_hostname is True
    assert imap.call_args.kwargs["byte_budget"] == 1024 * 1024
    assert smtp.call_args.kwargs["context"].check_hostname is True


def test_test_failure_does_not_echo_server_or_secret(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch("easy_email_mcp.web_config.BoundedIMAP4SSL", side_effect=RuntimeError("FAKE_SECRET")), patch("easy_email_mcp.web_config.smtplib.SMTP_SSL", side_effect=RuntimeError("FAKE_SECRET")):
        result = client.post("/api/test", json={"username":"user@example.com", "password":"FAKE_SECRET"}, headers=headers)
    assert result.status_code == 200
    assert "FAKE_SECRET" not in result.text
    assert result.json()["imap"].startswith("failed")
    assert not path.exists()


def test_keyring_save_failure_keeps_existing_config(tmp_path):
    client, path, headers = setup(tmp_path)
    original = '{"username":"old@example.com"}'
    path.write_text(original)
    backend = type("TestBackend", (), {"__module__":"keyring.backends.SecretService"})()
    with patch("keyring.get_keyring", return_value=backend), patch("keyring.set_password", side_effect=RuntimeError("FAKE_SECRET")):
        response = client.post("/api/config", json={"username":"user@example.com", "password":"FAKE_SECRET"}, headers=headers)
    assert response.status_code == 503
    assert path.read_text() == original
    assert "FAKE_SECRET" not in response.text
    assert not list(tmp_path.glob(".email-config-*"))


def test_partial_secret_success_config_commit_failure_is_explicit(tmp_path):
    client, path, headers = setup(tmp_path)
    original = '{"username":"old@example.com"}'
    path.write_text(original)
    backend = type("TestBackend", (), {"__module__":"keyring.backends.SecretService"})()
    with patch("keyring.get_keyring", return_value=backend), patch("keyring.set_password") as saved, patch("easy_email_mcp.web_config.os.replace", side_effect=OSError("FAKE_SECRET")):
        response = client.post("/api/config", json={"username":"user@example.com", "password":"FAKE_SECRET"}, headers=headers)
    saved.assert_called_once()
    assert response.status_code == 500 and response.json()["partial"] is True
    assert "FAKE_SECRET" not in response.text
    assert path.read_text() == original
    assert not list(tmp_path.glob(".email-config-*"))


def test_starttls_is_required_before_smtp_login(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch("easy_email_mcp.web_config.BoundedIMAP4SSL"), patch("easy_email_mcp.web_config.smtplib.SMTP") as smtp:
        response = client.post("/api/test", json={"username":"user@example.com", "password":"FAKE", "smtp_security":"starttls", "smtp_port":587}, headers=headers)
    assert response.json()["smtp"] == "authenticated"
    assert [c[0] for c in smtp.return_value.method_calls] == ["ehlo", "starttls", "ehlo", "login", "quit"]
    assert smtp.return_value.starttls.call_args.kwargs["context"].check_hostname is True


def test_partial_configuration_get_merges_defaults(tmp_path):
    client, path, headers = setup(tmp_path)
    path.write_text('{"username":"user@example.com"}')
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["imap_port"] == 993
    assert response.json()["timeout"] == 20
    assert client.post("/api/config", json=response.json(), headers=headers).status_code == 200
