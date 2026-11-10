"""Hot reload never restarts the Agent, reads real credentials or contacts mail."""
import asyncio
import json
import os
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from mcp import Client
import pytest
from easy_email_mcp.config import Config, MailError
from easy_email_mcp.runtime import ConfigRuntime, mailbox_context
from easy_email_mcp.server import create_server


def atomic_config(path, **values):
    temp = path.with_suffix('.new')
    temp.write_text(json.dumps({'username': 'first@example.invalid', **values}))
    os.replace(temp, path)


@pytest.fixture
def source(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('EASY_EMAIL_'):
            monkeypatch.delenv(key)
    path = tmp_path / 'config.json'
    atomic_config(path)
    monkeypatch.setenv('EASY_EMAIL_CONFIG', str(path))
    return path


def result_body(result):
    assert not result.is_error
    return json.loads(result.content[0].text)


def test_next_call_reloads_and_startup_environment_still_wins(source, monkeypatch):
    monkeypatch.setenv('EASY_EMAIL_IMAP_PORT', '1993')
    runtime = ConfigRuntime()
    first = runtime.snapshot()
    atomic_config(source, username='second@example.invalid', imap_port=993)
    latest = runtime.snapshot()
    assert first.config.username == 'first@example.invalid'
    assert latest.config.username == 'second@example.invalid'
    assert first is not latest and first.revision != latest.revision
    assert latest.config.imap_port == 1993
    assert runtime.status()['environment_override_fields'] == ['imap_port']
    assert runtime.status()['configuration_reload'] == 'before_each_tool_call'


def test_send_disabled_at_start_is_capability_ceiling(source):
    runtime = ConfigRuntime()
    atomic_config(source, send_enabled=True)
    status = runtime.status()
    assert status['requested_send_enabled'] is True
    assert status['send_enabled'] is False
    assert status['send_tool_registered'] is False
    assert status['host_reconnect_required_for_send'] is True


def test_bad_reload_fails_closed_and_recovers_without_restart(source):
    runtime = ConfigRuntime()
    source.write_text('{bad JSON')
    with pytest.raises(MailError): runtime.snapshot()
    atomic_config(source, username='fixed@example.invalid')
    assert runtime.snapshot().config.username == 'fixed@example.invalid'


def test_revision_never_reads_or_hashes_password(source, monkeypatch):
    monkeypatch.setenv('EASY_EMAIL_PASSWORD', 'FAKE_PASSWORD_ONE')
    runtime = ConfigRuntime()
    first = runtime.status()['config_revision']
    assert 'EASY_EMAIL_PASSWORD' not in runtime.env
    monkeypatch.setenv('EASY_EMAIL_PASSWORD', 'FAKE_PASSWORD_TWO')
    with patch.object(Config, 'password', side_effect=AssertionError('must not retrieve secret')):
        assert runtime.status()['config_revision'] == first


def test_inflight_call_keeps_immutable_old_snapshot(source):
    entered, resume = threading.Event(), threading.Event()
    class FakeService:
        def __init__(self, cfg): self.config = cfg
        def long_read(self):
            entered.set()
            assert resume.wait(5)
            return self.config.username
    with patch('easy_email_mcp.runtime.MailService', FakeService):
        runtime = ConfigRuntime()
        def call(): return runtime.snapshot().service.long_read()
        with ThreadPoolExecutor() as pool:
            future = pool.submit(call)
            try:
                assert entered.wait(5)
                atomic_config(source, username='second@example.invalid', send_enabled=True)
                current = runtime.snapshot()
                assert current.config.username == 'second@example.invalid'
                assert current.config.send_enabled is False
            finally:
                resume.set()
            assert future.result(timeout=5) == 'first@example.invalid'


def test_status_and_bad_reload_through_live_mcp_session(source):
    async def run():
        async with Client(create_server()) as client:
            initial = result_body(await client.call_tool('mail_status', {}))
            source.write_text('{broken')
            failed = await client.call_tool('mail_status', {})
            assert failed.is_error
            atomic_config(source, username='second@example.invalid', send_enabled=True)
            latest = result_body(await client.call_tool('mail_status', {}))
            assert latest['config_revision'] != initial['config_revision']
            assert latest['send_enabled'] is False and latest['host_reconnect_required_for_send'] is True
            assert 'mail_send' not in [tool.name for tool in (await client.list_tools()).tools]
    asyncio.run(run())


def test_cross_account_uid_context_rejected(source):
    calls = []
    class FakeService:
        def __init__(self, cfg): self.config = cfg
        def search(self, *args): return {'uids': ['1'], 'uidvalidity': '7'}
        def read_message(self, *args):
            calls.append(self.config.username); return {'text': 'fixture'}
        def get_attachment(self, *args):
            calls.append('attachment'); return {'base64': ''}
    async def run():
        with patch('easy_email_mcp.runtime.MailService', FakeService):
            async with Client(create_server()) as client:
                old = result_body(await client.call_tool('mail_search', {}))
                atomic_config(source, username='second@example.invalid')
                args = {'folder': 'INBOX', 'uid': '1', 'uidvalidity': '7', 'mailbox_context': old['mailbox_context']}
                assert (await client.call_tool('mail_read', args)).is_error
                assert (await client.call_tool('mail_attachment', {**args, 'part_index': 1})).is_error
                assert calls == []
                fresh = result_body(await client.call_tool('mail_search', {}))
                args['mailbox_context'] = fresh['mailbox_context']
                assert not (await client.call_tool('mail_read', args)).is_error
                assert calls == ['second@example.invalid']
    asyncio.run(run())


def test_context_is_stable_across_cli_process_lifetimes(source):
    first = ConfigRuntime().snapshot().mailbox_context
    assert first == ConfigRuntime().snapshot().mailbox_context
    atomic_config(source, username='different@example.invalid')
    second = ConfigRuntime()
    with pytest.raises(MailError): second.checked_mailbox(first)
    assert second.snapshot().mailbox_context != first


def test_send_toggle_off_and_stale_approval_fail_without_network(source):
    atomic_config(source, send_enabled=True)
    async def run():
        async with Client(create_server()) as client:
            initial = result_body(await client.call_tool('mail_status', {}))
            assert 'mail_send' in [tool.name for tool in (await client.list_tools()).tools]
            atomic_config(source, send_enabled=False, username='different@example.invalid')
            args = {'to': ['fixture@example.invalid'], 'subject': 'fixture', 'body': 'fixture', 'request_id': 'fixture_request_1234', 'config_revision': initial['config_revision'], 'user_approved': True}
            with patch.object(Config, 'password', side_effect=AssertionError('must not retrieve secret')), patch('socket.create_connection', side_effect=AssertionError('network forbidden')):
                assert (await client.call_tool('mail_send', args)).is_error
                latest = result_body(await client.call_tool('mail_status', {}))
                assert latest['send_enabled'] is False
                args['config_revision'] = latest['config_revision']
                assert (await client.call_tool('mail_send', args)).is_error
    asyncio.run(run())


def test_explicit_config_is_intentionally_fixed(source):
    runtime = ConfigRuntime(Config(username='fixed@example.invalid'))
    atomic_config(source, username='changed@example.invalid')
    assert runtime.snapshot().config.username == 'fixed@example.invalid'
    assert runtime.status()['configuration_reload'] == 'fixed_explicit_config'


def test_web_save_applies_on_next_mcp_call_without_restarting(source):
    import re
    from starlette.testclient import TestClient
    from easy_email_mcp.web_config import create_app, PAGE
    web = TestClient(create_app(source), base_url='http://127.0.0.1:8765', client=('127.0.0.1', 1234))
    token = re.search("const token='([^']+)'", web.get('/').text).group(1)
    headers = {'Origin': 'http://127.0.0.1:8765', 'X-CSRF-Token': token}
    calls = []
    class FakeService:
        def __init__(self, cfg): self.config = cfg
        def list_folders(self):
            calls.append(self.config.username); return {'folders': []}
    async def run():
        with patch('easy_email_mcp.runtime.MailService', FakeService):
            async with Client(create_server()) as client:
                before = result_body(await client.call_tool('mail_status', {}))
                response = web.post('/api/config', json={'username': 'saved@example.invalid'}, headers=headers)
                assert response.status_code == 200
                assert response.json()['application_mode'] == 'next_tool_call'
                assert response.json()['running_process_verified'] is False
                after = result_body(await client.call_tool('mail_status', {}))
                assert after['config_revision'] != before['config_revision']
                assert not (await client.call_tool('mail_list_folders', {})).is_error
                assert calls == ['saved@example.invalid']
    assert '保存并应用配置' in PAGE
    assert 'MCP 进程需重新启动才会加载新配置' not in PAGE
    with patch.object(Config, 'password', side_effect=AssertionError('no real credentials')):
        asyncio.run(run())


def test_inflight_send_snapshot_is_not_rebound_and_new_calls_are_disabled(source):
    atomic_config(source, send_enabled=True)
    entered, resume = threading.Event(), threading.Event()
    class FakeService:
        def __init__(self, cfg): self.config = cfg
        def send(self):
            if not self.config.send_enabled:
                raise MailError('Sending disabled')
            entered.set()
            assert resume.wait(5)
            return self.config.username
    with patch('easy_email_mcp.runtime.MailService', FakeService):
        runtime = ConfigRuntime()
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(lambda: runtime.snapshot().service.send())
            try:
                assert entered.wait(5)
                atomic_config(source, username='second@example.invalid', send_enabled=False)
                with pytest.raises(MailError): runtime.snapshot().service.send()
            finally:
                resume.set()
            # Already-started operations finish against their approved snapshot;
            # saving a file is not a cancellation or a rollback of SMTP activity.
            assert pending.result(timeout=5) == 'first@example.invalid'


def test_real_stdio_process_reloads_same_session(source):
    import sys
    from mcp.client.stdio import StdioServerParameters
    async def run():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'easy_email_mcp.server'], env=dict(os.environ))
        async with Client(params) as client:
            first = result_body(await client.call_tool('mail_status', {}))
            assert first['version'] == '0.3.0'
            atomic_config(source, username='new@example.invalid', send_enabled=True)
            second = result_body(await client.call_tool('mail_status', {}))
            assert second['config_revision'] != first['config_revision']
            assert second['configuration_reload'] == 'before_each_tool_call'
            assert second['host_reconnect_required_for_send'] is True
            assert second['send_enabled'] is False
            assert 'mail_send' not in [tool.name for tool in (await client.list_tools()).tools]
    asyncio.run(run())


def test_relative_ledger_uses_config_directory_across_cwds(source, monkeypatch, tmp_path):
    atomic_config(source, state_dir='private-state')
    first = ConfigRuntime().snapshot()
    other = tmp_path / 'other';other.mkdir()
    monkeypatch.chdir(other)
    second = ConfigRuntime().snapshot()
    assert first.config.state_dir == str(source.parent / 'private-state')
    assert second.config.state_dir == first.config.state_dir
    assert second.revision == first.revision


def test_environment_relative_ledger_without_file_uses_startup_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv('EASY_EMAIL_CONFIG', raising=False)
    monkeypatch.setenv('EASY_EMAIL_STATE_DIR', 'ledger')
    monkeypatch.chdir(tmp_path)
    runtime = ConfigRuntime()
    first = runtime.snapshot()
    other = tmp_path / 'other';other.mkdir();monkeypatch.chdir(other)
    assert runtime.snapshot().revision == first.revision
    assert ConfigRuntime().snapshot().revision != first.revision


def test_status_identity_is_nonsecret_and_revision_is_content_hash(source):
    runtime = ConfigRuntime()
    first = runtime.status()
    assert first['smtp_identity']['username'] == 'first@example.invalid'
    assert 'password' not in first['smtp_identity']
    atomic_config(source, username='second@example.invalid')
    assert runtime.status()['config_revision'] != first['config_revision']
    atomic_config(source)
    assert runtime.status()['config_revision'] == first['config_revision']
