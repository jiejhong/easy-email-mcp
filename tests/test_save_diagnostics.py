"""Safe error classification with mocked credential stores; no real credentials."""
from unittest.mock import patch
import pytest
from test_web_config import setup


@pytest.mark.parametrize('changes,field', [
    ({'imap_port': 0}, 'imap_port'), ({'smtp_port': True}, 'smtp_port'),
    ({'smtp_security': 'none'}, 'smtp_security'), ({'username': ''}, 'username'),
    ({'timeout': 0}, 'timeout'), ({'max_message_bytes': '8'}, 'max_message_bytes'),
    ({'secret_source': 'env', 'password': 'TEST_ONLY_SECRET'}, 'secret_source'),
])
def test_nonsecret_validation_is_distinct(tmp_path, changes, field):
    client, path, headers = setup(tmp_path)
    with patch('keyring.set_password') as write:
        response = client.post('/api/config', json={'username': 'fixture@example.invalid', **changes}, headers=headers)
    assert response.status_code == 400
    assert response.json()['stage'] == 'validate'
    assert response.json()['field'] == field
    assert field in response.json()['error']
    assert 'TEST_ONLY_SECRET' not in response.text
    assert not path.exists()
    write.assert_not_called()


@pytest.mark.parametrize('module', ['keyring.backends.chainer', 'keyrings.alt.file'])
def test_chain_or_plaintext_not_silently_allowed(tmp_path, module):
    client, path, headers = setup(tmp_path)
    backend = type('FixtureBackend', (), {'__module__': module})()
    with patch('keyring.get_keyring', return_value=backend), patch('keyring.set_password') as write:
        response = client.post('/api/config', json={'username':'fixture@example.invalid','password':'TEST_ONLY_SECRET'}, headers=headers)
    assert response.json()['code'] == 'keyring_backend_unsupported'
    assert response.json()['stage'] == 'backend_select'
    assert 'TEST_ONLY_SECRET' not in response.text
    write.assert_not_called()


def test_backend_initialization_failure_is_separate(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch('keyring.get_keyring', side_effect=RuntimeError('TEST_ONLY_SECRET')):
        response = client.post('/api/config', json={'username':'fixture@example.invalid','password':'TEST_ONLY_SECRET'}, headers=headers)
    assert response.status_code == 503
    assert response.json()['code'] == 'keyring_backend_unavailable'
    assert 'TEST_ONLY_SECRET' not in response.text


def test_credential_failure_keeps_previous_config_and_hides_exception(tmp_path, caplog):
    client, path, headers = setup(tmp_path)
    original = '{"username":"old@example.invalid"}'
    path.write_text(original)
    backend = type('FixtureBackend', (), {'__module__':'keyring.backends.Windows'})()
    with patch('keyring.get_keyring', return_value=backend), patch('keyring.set_password', side_effect=PermissionError('TEST_ONLY_SECRET')):
        response = client.post('/api/config', json={'username':'fixture@example.invalid','password':'TEST_ONLY_SECRET'}, headers=headers)
    assert response.status_code == 503
    assert response.json()['code'] == 'credential_write_failed'
    assert response.json()['stage'] == 'credential_write'
    assert path.read_text() == original
    assert 'TEST_ONLY_SECRET' not in response.text
    assert 'TEST_ONLY_SECRET' not in caplog.text
    assert 'credential_write_failed' in caplog.text
    assert not list(tmp_path.glob('.email-config-*'))


def test_directory_write_failure_never_attempts_credentials(tmp_path):
    client, path, headers = setup(tmp_path)
    with patch('easy_email_mcp.web_config.tempfile.mkstemp', side_effect=PermissionError('TEST_ONLY_SECRET')), patch('keyring.set_password') as write:
        response = client.post('/api/config', json={'username':'fixture@example.invalid','password':'TEST_ONLY_SECRET'}, headers=headers)
    assert response.status_code == 500
    assert response.json()['code'] == 'config_file_write_failed'
    assert response.json()['stage'] == 'config_write'
    assert 'TEST_ONLY_SECRET' not in response.text
    write.assert_not_called()


def test_invalid_json_has_safe_diagnostic(tmp_path):
    client, path, headers = setup(tmp_path)
    response = client.post('/api/config', content='{"password":"TEST_ONLY_SECRET"', headers={**headers,'Content-Type':'application/json'})
    assert response.json()['code'] == 'invalid_json'
    assert 'TEST_ONLY_SECRET' not in response.text
