from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOKEN = "tok-123"


def _run(stdin: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LF_AGY_")}
    env.update(env_extra)
    # -I ignores PYTHONPATH and the venv's editable install may point at another
    # checkout, so put this worktree's src on sys.path inside the isolated child.
    boot = (f"import sys, runpy; sys.path.insert(0, {str(REPO / 'src')!r}); "
            "runpy.run_module('lightfall.agents.backends.agy.hook_client', run_name='__main__')")
    return subprocess.run(
        [sys.executable, "-I", "-c", boot],
        input=stdin, capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60)


def _assert_denied(proc):
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "deny"
    assert out["reason"].startswith("Lightfall unreachable:")


def test_hook_client_forwards_and_prints():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            seen["path"] = self.path
            seen["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if seen["auth"] != f"Bearer {TOKEN}":
                self.send_response(401)
                self.end_headers()
                return
            data = json.dumps({"decision": "allow", "reason": "fine"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        payload = json.dumps({"toolCall": {"name": "x", "args": {}}})
        proc = _run(payload, {"LF_AGY_PORT": str(srv.server_port), "LF_AGY_TOKEN": TOKEN})
    finally:
        srv.shutdown()
        srv.server_close()
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"decision": "allow", "reason": "fine"}
    assert seen["path"] == "/hook"
    assert seen["body"] == payload


def test_hook_client_fails_closed():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _assert_denied(_run("{}", {"LF_AGY_PORT": str(port), "LF_AGY_TOKEN": TOKEN}))
    _assert_denied(_run("{}", {}))


def _serve(handler_fn):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            code, data = handler_fn()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_hook_client_fails_closed_on_bad_responses():
    cases = [(200, b"not json"), (401, b"{}"), (500, b'{"decision":"allow"}'),
             (200, b'{"reason":"no decision key"}'), (200, b"[1]")]
    for code, data in cases:
        srv = _serve(lambda code=code, data=data: (code, data))
        try:
            _assert_denied(_run("{}", {"LF_AGY_PORT": str(srv.server_port), "LF_AGY_TOKEN": TOKEN}))
        finally:
            srv.shutdown()
            srv.server_close()


def test_hook_client_ignores_proxy_settings():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    proxy = f"http://127.0.0.1:{dead}"
    srv = _serve(lambda: (200, b'{"decision":"allow","reason":"direct"}'))
    try:
        env = {"LF_AGY_PORT": str(srv.server_port), "LF_AGY_TOKEN": TOKEN,
               "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
               "http_proxy": proxy, "https_proxy": proxy, "all_proxy": proxy}
        proc = _run("{}", env)
    finally:
        srv.shutdown()
        srv.server_close()
    assert json.loads(proc.stdout) == {"decision": "allow", "reason": "direct"}
