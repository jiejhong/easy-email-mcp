"""Loopback-only configuration UI. Never expose this app with a public proxy."""
from __future__ import annotations

import argparse
import hmac
import smtplib
import json
import os
from pathlib import Path
import secrets
import tempfile

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Config, MailError, tls_context
from .mail import BoundedIMAP4SSL

MAX_REQUEST = 16 * 1024
FIELDS = set(Config.__dataclass_fields__)


def validate(data):
    if not isinstance(data, dict) or set(data) - FIELDS - {"password"}:
        raise ValueError("Unsupported configuration fields")
    cfg = {k: v for k, v in data.items() if k in FIELDS}
    defaults = Config()
    for k in FIELDS:
        cfg.setdefault(k, getattr(defaults, k))
    for k in ("imap_host", "smtp_host", "username", "secret_env", "state_dir"):
        if not isinstance(cfg[k], str) or len(cfg[k]) > 1024 or any(ord(c) < 32 for c in cfg[k]):
            raise ValueError("Invalid text field")
    for k in ("imap_host", "smtp_host"):
        if not cfg[k] or any(c in cfg[k] for c in " /\\@?#"):
            raise ValueError("Use a hostname, not a URL")
    if not cfg["username"] or len(cfg["username"]) > 320:
        raise ValueError("Mailbox username required")
    for k in ("imap_port", "smtp_port"):
        if type(cfg[k]) is not int or not 1 <= cfg[k] <= 65535:
            raise ValueError("Invalid server port")
    if cfg["smtp_security"] not in ("tls", "starttls") or cfg["secret_source"] not in ("keyring", "env"):
        raise ValueError("Invalid TLS or secret configuration")
    if type(cfg["send_enabled"]) is not bool:
        raise ValueError("send_enabled must be boolean")
    if not isinstance(cfg["recipients"], (list, tuple)) or len(cfg["recipients"]) > 100:
        raise ValueError("Invalid recipients")
    if any(not isinstance(x, str) or len(x) > 320 or any(ord(c) < 32 for c in x) for x in cfg["recipients"]):
        raise ValueError("Invalid recipients")
    try:
        Config.from_mapping(cfg)
    except MailError:
        raise ValueError("Invalid configuration") from None
    password = data.get("password", "")
    if not isinstance(password, str) or len(password) > 4096:
        raise ValueError("Invalid password input")
    if password and cfg["secret_source"] != "keyring":
        raise ValueError("Web passwords require an approved OS keyring")
    return cfg, password


class PartialSaveError(Exception):
    """Password was stored, but configuration commit failed."""


def save_config(path: Path, cfg: dict, password: str):
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Configuration symlinks are not supported")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".email-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if password:
            import keyring
            backend = keyring.get_keyring()
            if not backend.__class__.__module__.startswith(("keyring.backends.SecretService", "keyring.backends.macOS", "keyring.backends.Windows")):
                raise ValueError("No approved OS keyring; password was not saved")
            try:
                keyring.set_password("easy-email-mcp", cfg["username"], password)
            except Exception:
                raise ValueError("OS keyring unavailable; configuration was not saved") from None
        try:
            os.replace(temporary, path)
        except OSError:
            if password:
                raise PartialSaveError() from None
            raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def test_connection(cfg: dict, supplied_password: str):
    """Explicit authentication check only: never read messages or send mail."""
    config = Config.from_mapping(cfg)
    try:
        password = supplied_password or config.password()
    except Exception:
        return {"imap": "not_tested", "smtp": "not_tested", "error": "Approved mailbox secret unavailable"}
    results = {}
    for protocol in ("imap", "smtp"):
        connection = None
        try:
            if protocol == "imap":
                connection = BoundedIMAP4SSL(config.imap_host, config.imap_port, ssl_context=tls_context(), timeout=min(config.timeout, 10), byte_budget=1024 * 1024)
                connection.login(config.username, password)
            else:
                if config.smtp_security == "tls":
                    connection = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, context=tls_context(), timeout=min(config.timeout, 10))
                else:
                    connection = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=min(config.timeout, 10))
                    connection.ehlo()
                    connection.starttls(context=tls_context())
                    connection.ehlo()
                connection.login(config.username, password)
            results[protocol] = "authenticated"
        except Exception:
            results[protocol] = "failed: connection, TLS or authentication error"
        finally:
            if connection is not None:
                try:
                    if protocol == "imap":
                        connection.logout()
                    else:
                        connection.quit()
                except Exception:
                    try:
                        connection.shutdown() if protocol == "imap" else connection.close()
                    except Exception:
                        pass
    return results


def create_app(config_path: str | Path, port: int = 8765):
    path = Path(config_path)
    token = secrets.token_urlsafe(32)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    async def guard(request: Request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"} or request.headers.get("host", "") not in hosts:
            return JSONResponse({"error": "Loopback access only"}, status_code=403)
        if request.method != "GET":
            if request.headers.get("origin", "") not in origins or not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode("utf-8"), token.encode("ascii")):
                return JSONResponse({"error": "Origin or CSRF check failed"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    async def home(request):
        return HTMLResponse(PAGE.replace("__TOKEN__", token))

    async def get_config(request):
        try:
            if not path.exists():
                value = {k: getattr(Config(), k) for k in FIELDS}
            else:
                if path.is_symlink() or path.stat().st_size > MAX_REQUEST:
                    raise ValueError()
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or set(value) - FIELDS:
                    raise ValueError()
            value = vars(Config.from_mapping(value))
            return JSONResponse(value)
        except Exception:
            return JSONResponse({"error": "Configuration cannot be safely read"}, status_code=400)

    async def post_config(request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "JSON required"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST:
                return JSONResponse({"error": "Request too large"}, status_code=413)
            body.extend(chunk)
        try:
            cfg, password = validate(json.loads(body))
            if request.url.path == "/api/test":
                return JSONResponse(await run_in_threadpool(test_connection, cfg, password))
            await run_in_threadpool(save_config, path, cfg, password)
            return JSONResponse({"saved": True, "message": "Configuration saved. Restart the MCP process to apply it. No mailbox connection was tested."})
        except PartialSaveError:
            return JSONResponse({"partial": True, "error": "Password was saved to the OS keyring, but the configuration file was not committed. Check file access before retrying; the previous password was not restored."}, status_code=500)
        except ValueError:
            return JSONResponse({"error": "Invalid settings or approved OS keyring unavailable. No plaintext password fallback."}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Could not save configuration. Check local file and credential-store access."}, status_code=500)

    app = Starlette(routes=[Route("/", home), Route("/api/config", get_config), Route("/api/config", post_config, methods=["POST"]), Route("/api/test", post_config, methods=["POST"])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=guard)
    return app


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>easy-email-mcp 配置</title><style>body{font:16px system-ui;max-width:720px;margin:2rem auto;padding:1rem}label{display:block;margin:1rem 0}input,select,textarea{display:block;width:95%;padding:.5rem}button{padding:.7rem}small{color:#555}</style><h1>easy-email-mcp 配置</h1><p>仅限本机访问。保存配置不代表已登录邮箱。只有点击“测试连接”才会验证 IMAP/SMTP 登录，不收取邮件、不发信。</p><form id="settings"><div id="fields"></div><label>密码（可选，仅保存到系统安全凭据库；留空保持原密码）<input name="password" type="password" autocomplete="new-password"></label><button type="submit" value="save">保存配置</button> <button type="submit" value="test">测试连接</button></form><p id="result" role="status"></p><small>不要将本页面通过公网代理公开。环境变量覆盖配置文件；MCP 进程需重新启动才会加载新配置。</small><script nonce="__TOKEN__">
const token='__TOKEN__', form=document.querySelector('form'), fields=document.querySelector('#fields'), result=document.querySelector('#result');
const labels={imap_host:'IMAP 服务器',imap_port:'IMAP TLS 端口',smtp_host:'SMTP 服务器',smtp_port:'SMTP 端口',smtp_security:'SMTP 加密方式',username:'邮箱账号',secret_source:'密码来源',secret_env:'密码环境变量名（不是密码）',send_enabled:'允许发送（仍须 Agent 明确授权）',recipients:'允许收件人（逗号分隔）',state_dir:'本地状态目录',max_message_bytes:'邮件字节上限',max_attachment_bytes:'附件字节上限',timeout:'连接超时（秒）'};
fetch('/api/config').then(r=>r.json()).then(data=>{if(data.error)throw Error(data.error);Object.entries(labels).forEach(([name,label])=>{const l=document.createElement('label');l.textContent=label;let e;if(['smtp_security','secret_source','send_enabled'].includes(name)){e=document.createElement('select');let opts=name==='smtp_security'?['tls','starttls']:name==='secret_source'?['keyring','env']:['false','true'];opts.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;e.append(o)});}else{e=document.createElement('input');e.type=(name.endsWith('_port')||name.endsWith('_bytes')||name==='timeout')?'number':'text';}e.name=name;e.value=Array.isArray(data[name])?data[name].join(','):String(data[name]??'');l.append(e);fields.append(l);});}).catch(e=>{result.textContent='配置读取失败';form.querySelector('button').disabled=true;});
form.addEventListener('submit',async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(form));d.imap_port=Number(d.imap_port);d.smtp_port=Number(d.smtp_port);d.max_message_bytes=Number(d.max_message_bytes);d.max_attachment_bytes=Number(d.max_attachment_bytes);d.timeout=Number(d.timeout);d.send_enabled=d.send_enabled==='true';d.recipients=d.recipients.split(',').map(x=>x.trim()).filter(Boolean);try{const r=await fetch(e.submitter?.value==='test'?'/api/test':'/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(d)});const data=await r.json();result.textContent=data.message||data.error||('IMAP: '+data.imap+'；SMTP: '+data.smtp+'（未保存配置）');form.elements.password.value='';}catch(e){result.textContent='保存失败，请检查本机配置服务';form.elements.password.value='';}});
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description="Loopback-only email MCP configuration UI")
    parser.add_argument("--config", default=os.getenv("EASY_EMAIL_CONFIG", str(Path.home() / ".config" / "easy-email-mcp" / "config.json")))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged local port (1024-65535)")
    import uvicorn
    uvicorn.run(create_app(args.config, args.port), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
