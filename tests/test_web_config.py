import json
import os
import re
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from starlette.testclient import TestClient
from easy_email_mcp.web_config import create_app
from easy_email_mcp.config import Config, keyring_service


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
    save.assert_called_once_with(keyring_service(Config(username="user@example.com")), "user@example.com", "FAKE_TEST_ONLY")
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


def test_connection_info_is_real_stdio_not_web_url_and_no_secrets(tmp_path, monkeypatch):
    client, path, _ = setup(tmp_path)
    monkeypatch.setenv('EASY_EMAIL_PASSWORD', 'SECRET_MUST_NOT_APPEAR')
    path.write_text('{"username":"PRIVATE_ACCOUNT", "secret_env":"PRIVATE_SECRET_VARIABLE"}')
    with patch('keyring.get_password', side_effect=AssertionError('must not read secrets')):
        response = client.get('/api/connection')
        page = client.get('/')
    assert response.status_code == 200
    data = response.json()
    assert data['transport'] == 'stdio' and data['mcp_url'] is None
    assert data['configuration_page_url'] == 'http://127.0.0.1:8765/'
    assert data['process_verified'] is False
    server = data['mcp_configuration']['mcpServers']['easy-email-mcp']
    assert server['args'] == ['-m', 'easy_email_mcp.server']
    assert server['env'] == {'EASY_EMAIL_CONFIG': str(path.absolute())}
    for value in ('SECRET_MUST_NOT_APPEAR', 'PRIVATE_ACCOUNT', 'PRIVATE_SECRET_VARIABLE'):
        assert value not in response.text + page.text
    assert '不是 MCP 服务地址' in page.text
    assert 'MCP HTTP / SSE 地址' in page.text
    assert '未提供' in page.text
    assert 'mcp-config-example' in page.text
    assert response.headers['cache-control'] == 'no-store'


def test_connection_panel_escapes_paths_and_handles_posix_quoting(tmp_path):
    from easy_email_mcp.web_config import connection_info, connection_panel
    import shlex
    path = PurePosixPath("/tmp/name with 'quotes' <script>alert(1)</script>/config.json")
    info = connection_info(path, 8765, executable="/tmp/venv with spaces/bin/python", windows=False)
    split = shlex.split(info['launch_command'])
    assert split == ['EASY_EMAIL_CONFIG=' + str(path), '/tmp/venv with spaces/bin/python', '-m', 'easy_email_mcp.server']
    panel = connection_panel(info)
    assert '<script>alert(1)</script>' not in panel
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in panel
    assert info['mcp_configuration']['mcpServers']['easy-email-mcp']['command'] == '/tmp/venv with spaces/bin/python'


def test_windows_connection_info_quotes_powershell_paths():
    from easy_email_mcp.web_config import connection_info
    path = r"C:\Users\O'Neil\Mail settings\config.json"
    executable = r"C:\Program Files\Python\python.exe"
    info = connection_info(path, 18765, executable=executable, windows=True)
    assert info['launch_shell'] == 'Windows PowerShell'
    assert info['launch_command'] == "$env:EASY_EMAIL_CONFIG = 'C:\\Users\\O''Neil\\Mail settings\\config.json'; & 'C:\\Program Files\\Python\\python.exe' '-m' 'easy_email_mcp.server'"
    assert info['mcp_configuration']['mcpServers']['easy-email-mcp']['env']['EASY_EMAIL_CONFIG'] == path
    assert info['configuration_page_url'] == 'http://127.0.0.1:18765/'
    assert info['mcp_url'] is None


def test_connection_endpoint_enforces_loopback_host_guard(tmp_path):
    client, path, _ = setup(tmp_path)
    assert client.get('/api/connection', headers={'Host': 'evil.example:8765'}).status_code == 403
    remote = TestClient(create_app(path), base_url='http://127.0.0.1:8765', client=('10.0.0.2', 1))
    assert remote.get('/api/connection').status_code == 403


def test_windows_smart_single_quotes_are_escaped_without_changing_paths():
    from easy_email_mcp.web_config import connection_info
    quote_chars = "'\u2018\u2019\u201a\u201b"
    # Exercise all recognized quote characters, including a shell-like suffix.
    # These are inert fixture strings; no command is executed.
    for quote in quote_chars:
        path = "C:\\Users\\" + quote + "; Write-Output INJECTED; #" + quote + "\\config.json"
        executable = "C:\\Python" + quote + "Path\\python.exe"
        info = connection_info(path, 8765, executable=executable, windows=True)
        escaped_path = "'" + path.replace(quote, quote + quote) + "'"
        escaped_exe = "'" + executable.replace(quote, quote + quote) + "'"
        assert info['launch_command'] == "$env:EASY_EMAIL_CONFIG = " + escaped_path + "; & " + escaped_exe + " '-m' 'easy_email_mcp.server'"
        server = info['mcp_configuration']['mcpServers']['easy-email-mcp']
        assert server['env']['EASY_EMAIL_CONFIG'] == path
        assert server['command'] == executable


def test_windows_mixed_single_quotes_round_trip_as_one_literal():
    from easy_email_mcp.web_config import connection_info
    # Mirror the documented PowerShell literal-token rule only; this is not
    # a substitute for Windows/PowerShell runtime interoperability testing.
    quote_chars = "'\u2018\u2019\u201a\u201b"
    path = "C:\\" + quote_chars + "“literal”;$env:PRIVATE;`$(data)\\config.json"
    command = connection_info(path, 8765, executable='python.exe', windows=True)['launch_command']
    start = len('$env:EASY_EMAIL_CONFIG = ')
    assert command[start] == "'"
    result, index = [], start + 1
    while index < len(command):
        char = command[index]
        if char in quote_chars:
            if index + 1 < len(command) and command[index + 1] in quote_chars:
                result.append(command[index + 1])
                index += 2
                continue
            break
        result.append(char)
        index += 1
    assert ''.join(result) == path
    assert command[index:] == "'; & 'python.exe' '-m' 'easy_email_mcp.server'"
