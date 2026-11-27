"""Exercise the deployed HTTP boundaries with an explicitly scripted model provider."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert self.headers["Authorization"] == "Bearer scripted-only"
        messages = payload["messages"]
        prefix = "Untrusted monitoring observations (JSON data only):\n"
        catalog = next(
            (json.loads(m["content"].removeprefix(prefix)) for m in messages
             if m["role"] == "user" and m["content"].startswith(prefix)),
            None,
        )
        evidence = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if catalog is not None:
            assert not payload.get("tools") and not evidence
            assert catalog["health.http"] == "HTTP 503"
            assert catalog["metrics.error_rate_percent"] == "Error rate 42.0%"
            assert catalog["metrics.pool"] == "Database pool 100/100 connections"
            assert any(ref.startswith("log.") and "pool exhausted" in fact for ref, fact in catalog.items())
            reply = self.selection_reply(style="technical")
        elif not evidence:
            assert payload.get("tools")
            reply = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": name,
                        "type": "function",
                        "function": {
                            "name": "get_service_" + name,
                            "arguments": json.dumps({"service": "orders"}),
                        },
                    }
                    for name in ("health", "logs", "metrics")
                ],
            }
        else:
            health = next(m for m in evidence if "http_status" in m)
            metrics = next(m["metrics"] for m in evidence if "metrics" in m)
            logs = next(m["logs"] for m in evidence if "logs" in m)
            assert (
                health["http_status"] == 503 and "pool exhausted" in logs[0]["message"]
            )
            assert metrics["error_rate_percent"] == 42.0 and metrics["active_db_connections"] == 100
            reply = self.selection_reply()
        body = json.dumps(
            {
                "id": "scripted",
                "object": "chat.completion",
                "created": 1,
                "model": "scripted",
                "choices": [
                    {
                        "index": 0,
                        "message": reply,
                        "finish_reason": "tool_calls" if "tool_calls" in reply else "stop",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def selection_reply(**presentation):
        return {"role": "assistant", "content": json.dumps({
            "observations": ["health.http", "metrics.error_rate_percent", "metrics.pool"],
            "hypotheses": [{"cause": "pool_saturation", "evidence": "metrics.pool"}],
            **presentation,
        })}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(module, env, url):
    with tempfile.TemporaryFile(mode="w+b") as log:
        yield from serve_with_log(module, env, url, log)


def serve_with_log(module, env, url, log):
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                assert proc.poll() is None, f"{module} exited during startup"
                try:
                    if client.get(url + "/healthz").is_success:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError(f"{module} did not start")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
        if sys.exc_info()[0] is not None:
            log.seek(0)
            print(f"{module} subprocess:\n{log.read().decode(errors='replace')}")


def test_two_servers_over_tcp(tmp_path):
    with ExitStack() as stack:
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        stack.callback(provider.server_close)
        stack.callback(provider.shutdown)
        backend_port, agent_port = free_port(), free_port()
        while agent_port == backend_port:
            agent_port = free_port()
        backend, agent = (f"http://127.0.0.1:{p}" for p in (backend_port, agent_port))
        env = {
            **os.environ,
            "PORT": str(backend_port),
            "AGENT_PORT": str(agent_port),
            "BACKEND_HOST": "127.0.0.1",
            "DATABASE_PATH": str(tmp_path / "live.sqlite3"),
            "LLM_MODEL": "scripted",
            "LLM_API_KEY": "scripted-only",
            "LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/v1",
            "MONITORING_API_BASE_URL": backend,
            "AGENT_CHAT_URL": agent + "/chat",
            "AGENT_API_KEY": "",
            "MONITORING_API_KEY": "test-reader",
            "PROTECT_READS": "true",
            "OPERATOR_PASSWORD": "test-operator",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        stack.enter_context(serve("backend.app.main", env, backend))
        stack.enter_context(serve("agent.main", env, agent))
        client = stack.enter_context(
            httpx.Client(base_url=backend, trust_env=False, timeout=65)
        )
        login = client.post("/api/session", json={"password": "test-operator"})
        client.headers["Authorization"] = "Bearer " + login.json()["token"]
        assert client.get("/docs").is_success
        assert client.post("/api/demo/reset", json={}).is_success
        assert client.post(
            "/api/demo/scenario",
            json={"service_id": "orders", "scenario": "database_pool_exhausted"},
        ).is_success
        chat = client.post(
            "/api/chat",
            json={
                "message": "Why is my order service failing?",
                "session_id": "demo",
                "service_id": "orders",
            },
        )
        assert chat.is_success, chat.text
        answer = chat.json()["response"]
        assert "HTTP 503" in answer and "42.0%" in answer and "100/100" in answer
        draft = client.post(
            "/api/incidents/draft",
            json={
                "session_id": "demo",
                "application_id": "demo",
                "service_id": "orders",
                "investigation": answer,
            },
        )
        assert draft.is_success, draft.text
        assert client.get("/api/incidents").json() == []
        report = client.post(
            "/api/incidents",
            json={**draft.json(), "confirmed": True, "approval_id": str(uuid4())},
        )
        assert report.status_code == 201
        assert client.get("/api/incidents").json()[0]["id"] == report.json()["id"]
        assert client.post("/api/demo/reset", json={"service_id": "orders"}).is_success
        assert client.get("/api/services/orders/health").json()["status"] == "healthy"
