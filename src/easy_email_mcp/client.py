"""Agent bridge: real official MCP client talking to a subprocess over stdio."""
import argparse
import asyncio
import json
import os
import sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from .attachments import MAX_SEND_ARGUMENT_BYTES


async def invoke(tool, arguments, mode="auto"):
    # Pass only operational variables and explicitly selected EASY_EMAIL settings.
    allowed = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "LOCALAPPDATA", "APPDATA", "LANG", "TMP", "TEMP", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"}
    secret_name = os.getenv("EASY_EMAIL_SECRET_ENV", "EASY_EMAIL_PASSWORD")
    env = {k: v for k, v in os.environ.items() if k in allowed or k.startswith("EASY_EMAIL_") or k == secret_name}
    params = StdioServerParameters(command=sys.executable, args=["-m", "easy_email_mcp.server"], env=env)
    async with Client(params, mode=mode, read_timeout_seconds=120) as client:
        result = await client.list_tools() if tool == "list" else await client.call_tool(tool, arguments)
        return {"protocol_version": client.protocol_version, "result": result.model_dump(mode="json", by_alias=True)}


def read_arguments(stream):
    """Bound binary stdin before JSON decoding; large approved attachments fit."""
    payload = stream.read(MAX_SEND_ARGUMENT_BYTES + 1)
    if len(payload) > MAX_SEND_ARGUMENT_BYTES:
        raise ValueError("Arguments too large")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Arguments must be an object")
    return data


def main():
    parser = argparse.ArgumentParser(description="Call the mail MCP from a cloud/local Agent; JSON arguments on stdin")
    parser.add_argument("tool", nargs="?", default="mail_status")
    parser.add_argument("--mode", choices=["auto", "legacy", "2026-07-28"], default="auto")
    parser.add_argument("--stdin", action="store_true", help="Read a JSON arguments object (max 48 MiB, including base64 attachments) from stdin")
    args = parser.parse_args()
    try:
        data = read_arguments(sys.stdin.buffer) if args.stdin else {}
        result = asyncio.run(invoke(args.tool, data, args.mode))
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print(json.dumps({"error": "MCP call failed; check configuration and trusted local diagnostics"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
