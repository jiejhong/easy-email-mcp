"""No real credential operations: isolate passwords across endpoint changes."""
from dataclasses import replace
import json
from unittest.mock import patch
import pytest
from easy_email_mcp.config import Config, MailError, keyring_service
from easy_email_mcp.runtime import ConfigRuntime
from easy_email_mcp.web_config import save_config


def native_backend():
    return type('FixtureBackend', (), {'__module__': 'keyring.backends.Windows'})()


def test_scoped_service_changes_with_identity_not_password(monkeypatch):
    cfg = Config(username='fixture@example.invalid')
    scope = keyring_service(cfg)
    assert scope.startswith('easy-email-mcp/v2/')
    for other in [replace(cfg, username='other@example.invalid'), replace(cfg, imap_host='new.example.invalid'),
                  replace(cfg, smtp_host='new.example.invalid'), replace(cfg, smtp_port=587, smtp_security='starttls')]:
        assert keyring_service(other) != scope
    monkeypatch.setenv('EASY_EMAIL_PASSWORD', 'FAKE_NO_HASH')
    with patch('keyring.get_password', side_effect=AssertionError('must not read secret')):
        assert keyring_service(cfg) == scope


def test_legacy_credential_is_never_read_or_migrated():
    cfg = Config(username='fixture@example.invalid')
    with patch('keyring.get_keyring', return_value=native_backend()), patch('keyring.get_password', return_value=None) as read, patch('keyring.set_password') as write, patch('keyring.delete_password') as delete:
        with pytest.raises(MailError, match='Save the password once'):
            cfg.password()
    read.assert_called_once_with(keyring_service(cfg), cfg.username)
    write.assert_not_called();delete.assert_not_called()


def test_new_endpoint_save_cannot_replace_old_inflight_snapshot_secret(tmp_path):
    old = Config(username='same@example.invalid', imap_host='old.example.invalid')
    new = replace(old, imap_host='new.example.invalid')
    store = {(keyring_service(old), old.username): 'FAKE_OLD', ('easy-email-mcp', old.username): 'FAKE_LEGACY'}
    def put(service, username, password): store[service, username] = password
    runtime = ConfigRuntime(old)
    snapshot = runtime.snapshot()  # in-flight operation captured this before save
    with patch('keyring.get_keyring', return_value=native_backend()), patch('keyring.set_password', side_effect=put), patch('keyring.get_password', side_effect=lambda service, username: store.get((service, username))), patch('keyring.delete_password') as delete:
        save_config(tmp_path / 'config.json', vars(new), 'FAKE_NEW')
        assert snapshot.config.password() == 'FAKE_OLD'
        assert new.password() == 'FAKE_NEW'
        assert store['easy-email-mcp', old.username] == 'FAKE_LEGACY'
        delete.assert_not_called()


@pytest.mark.parametrize('change', [
    {'imap_host': 'new.example.invalid'}, {'smtp_host': 'new.example.invalid'},
    {'username': 'new@example.invalid'}, {'secret_env': 'OTHER_SECRET'},
])
def test_env_identity_cannot_hot_change(tmp_path, monkeypatch, change):
    path = tmp_path / 'config.json'
    cfg = {'username': 'same@example.invalid', 'secret_source': 'env'}
    path.write_text(json.dumps(cfg));monkeypatch.setenv('EASY_EMAIL_CONFIG', str(path))
    runtime = ConfigRuntime()
    path.write_text(json.dumps({**cfg, **change}))
    with patch.object(Config, 'password', side_effect=AssertionError('no secret access')):
        with pytest.raises(MailError, match='pinned to the startup'):
            runtime.snapshot()


def test_keyring_to_env_transition_requires_new_process(tmp_path, monkeypatch):
    path = tmp_path / 'config.json';path.write_text('{"username":"fixture@example.invalid"}')
    monkeypatch.setenv('EASY_EMAIL_CONFIG', str(path));runtime = ConfigRuntime()
    path.write_text('{"username":"fixture@example.invalid","secret_source":"env"}')
    with pytest.raises(MailError, match='pinned to the startup'):
        runtime.snapshot()
