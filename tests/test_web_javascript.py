"""Exercise the shipped form handler, not a Python reimplementation of it."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from easy_email_mcp.web_config import PAGE


def test_actual_web_password_javascript():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for the actual UI JavaScript regression test')
    match = re.search(r'<script nonce="[^"]+">(.*?)</script>', PAGE, re.S)
    assert match
    script = match.group(1).replace('__TOKEN__', 'test-csrf-token')
    driver = Path(__file__).parent / 'js' / 'web_password_interaction.cjs'
    completed = subprocess.run(
        [node, str(driver)], input=json.dumps({'script': script}),
        text=True, capture_output=True, timeout=20, check=True,
    )
    assert json.loads(completed.stdout)['passed'] == 15
