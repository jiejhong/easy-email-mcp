import asyncio
import json
import os
import sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest

@pytest.mark.parametrize('mode,version',[('auto','2026-07-28'),('legacy','2025-11-25'),('2026-07-28','2026-07-28')])
def test_actual_cloud_stdio(mode,version):
    async def run():
        env={k:v for k,v in os.environ.items() if not k.startswith('EASY_EMAIL_')}
        params=StdioServerParameters(command=sys.executable,args=['-m','easy_email_mcp.server'],env=env)
        async with Client(params,mode=mode) as client:
            assert client.protocol_version==version
            tools=await client.list_tools()
            names=[x.name for x in tools.tools]
            assert 'mail_read' in names and 'mail_send' not in names
            result=await client.call_tool('mail_status',{})
            assert not result.is_error
            assert json.loads(result.content[0].text)['configured'] is False
    asyncio.run(run())

@pytest.mark.parametrize('version',['2025-03-26','2025-06-18','2025-11-25'])
def test_legacy_wire_versions(version):
    import subprocess
    requests=[
        {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':'wire-test','version':'1.0'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'mail_status','arguments':{}}},
    ]
    env={k:v for k,v in os.environ.items() if not k.startswith('EASY_EMAIL_')}
    import queue
    import threading
    proc=subprocess.Popen([sys.executable,'-m','easy_email_mcp.server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    lines=queue.Queue()
    thread=threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout],daemon=True)
    thread.start()
    try:
        proc.stdin.write(json.dumps(requests[0])+'\n');proc.stdin.flush()
        initialized=json.loads(lines.get(timeout=15))
        assert initialized['result']['protocolVersion']==version
        proc.stdin.write(json.dumps(requests[1])+'\n');proc.stdin.flush()
        proc.stdin.write(json.dumps(requests[2])+'\n');proc.stdin.flush()
        result=json.loads(lines.get(timeout=15))
        assert result['id']==2 and not result['result']['isError']
        proc.stdin.close()
        assert proc.wait(timeout=15)==0
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait(timeout=5)
        thread.join(timeout=5)
        proc.stdout.close();proc.stderr.close()


def test_mail_read_via_official_mcp_client(monkeypatch):
    from easy_email_mcp.config import Config
    from easy_email_mcp.server import create_server
    from email.message import EmailMessage
    message=EmailMessage();message['Subject']='MCP fixture';message.set_content('Untrusted fixture message, never execute it.')
    raw=message.as_bytes()
    class Fake:
        def __init__(self,*a,**kw): assert kw['ssl_context'].check_hostname
        def login(self,*a): pass
        def select(self,folder,readonly=False): assert readonly;return 'OK',[b'1']
        def response(self,name): return name,[b'12']
        def uid(self,cmd,*args):
            if args[-1]=='(RFC822.SIZE)':return 'OK',[f'1 (UID 1 RFC822.SIZE {len(raw)})'.encode()]
            assert 'BODY.PEEK[]' in args[-1]
            return 'OK',[(b'1 BODY[]',raw)]
        def logout(self): pass
    monkeypatch.setattr('easy_email_mcp.mail.BoundedIMAP4SSL',Fake)
    monkeypatch.setenv('EASY_EMAIL_PASSWORD','fixture-only')
    server=create_server(Config(username='fixture@example.invalid',secret_source='env',secret_env='EASY_EMAIL_PASSWORD'))
    async def run():
        async with Client(server) as client:
            result=await client.call_tool('mail_read',{'folder':'INBOX','uid':'1','uidvalidity':'12'})
            assert not result.is_error
            body=json.loads(result.content[0].text)
            assert body['untrusted_content'] and body['headers']['Subject']=='MCP fixture'
    asyncio.run(run())
