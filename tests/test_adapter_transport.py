"""Transport checks: mocked DNS/HTTP boundaries and a real loopback TLS handshake."""

import asyncio
import shutil
import ssl
import subprocess
from pathlib import Path

import anyio
import certifi
import httpx
import pytest
from fastapi import HTTPException

from backend.app import external
from backend.app.main import app

HOST = "monitor.example.com"
ORIGIN = "https://" + HOST
PUBLIC_IP = "93.184.216.34"
GET = dict(origin=ORIGIN, host=HOST, path="/health", accept="application/json", token=None)


@pytest.mark.anyio
@pytest.mark.parametrize("restricted", ["127.0.0.1", "10.1.1.1", "169.254.169.254", "192.0.2.1", "::1"])
async def test_dns_change_after_registration_fails_before_connect(monkeypatch, restricted):
    answers = iter([[PUBLIC_IP], [PUBLIC_IP, restricted]])

    async def dns(host, port, **_):
        assert (host, port) == (HOST, 443)
        return [(None, None, None, "", (ip, port)) for ip in next(answers)]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator")
    monkeypatch.setenv("APPROVED_HTTPS_ORIGINS", ORIGIN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as http:
        token = (await http.post("/api/session", json={"password": "operator"})).json()["token"]
        reg = await http.post("/api/applications", headers={"Authorization": "Bearer " + token},
                              json={"base_url": ORIGIN, "service_id": "s", "name": "S", "health_path": "/health"})
        assert reg.status_code == 201
        sid = reg.json()["service_ids"][0]
        monkeypatch.setattr(external.httpx, "AsyncClient", lambda **_: pytest.fail("Connected after DNS revocation"))
        result = await http.get(f"/api/services/{sid}/health")
        assert result.status_code == 403 and "restricted network" in result.text


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["redirect", "exact_limit", "over_limit", "read_timeout"])
async def test_pinned_response_boundaries(monkeypatch, case):
    seen = []
    async def dns(_):
        return PUBLIC_IP

    def remote(req):
        seen.append(req.url)
        assert req.url.host == PUBLIC_IP and req.headers["host"] == req.extensions["sni_hostname"] == HOST
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "http://169.254.169.254/"}, stream=httpx.ByteStream(b""))
        if case == "read_timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * (external.MAX_BYTES + (case == "over_limit"))))

    client = httpx.AsyncClient
    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False and kwargs["timeout"] == 4
        return client(transport=httpx.MockTransport(remote), **kwargs)

    monkeypatch.setattr(external, "resolve", dns)
    monkeypatch.setattr(external.httpx, "AsyncClient", factory)
    if case == "over_limit":
        with pytest.raises(HTTPException, match="64 KiB"):
            await external._read_pinned_get(**GET)
    elif case == "read_timeout":
        with pytest.raises(httpx.ReadTimeout):
            await external._read_pinned_get(**GET)
    else:
        code, _, data = await external._read_pinned_get(**GET)
        assert (code, len(data)) == ((307, 0) if case == "redirect" else (200, external.MAX_BYTES))
    assert len(seen) == 1


@pytest.mark.anyio
async def test_total_deadline_also_bounds_dns(monkeypatch):
    async def stalled(_):
        await asyncio.Event().wait()
    monkeypatch.setattr(external, "resolve", stalled)
    # Outer deadline distinguishes a missing production timeout from the expected one.
    with anyio.fail_after(8):
        with pytest.raises(TimeoutError):
            await external._read_pinned_get(**GET)


@pytest.fixture(scope="module")
def tls_cert(tmp_path_factory):
    executable = shutil.which("openssl")
    if not executable:
        git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if not git_openssl.exists():
            pytest.skip("Local TLS test requires the OpenSSL CLI")
        executable = str(git_openssl)
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run([executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=" + HOST, "-addext", "subjectAltName=DNS:" + HOST,
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return cert, key


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["matching", "wrong_hostname", "untrusted_issuer"])
async def test_pinned_tls_verifies_hostname_and_chain(monkeypatch, tls_cert, case):
    cert, key = tls_cert
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    names, requests = [], []
    server_context.set_servername_callback(lambda sock, name, ctx: names.append(name))

    async def serve(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_context)
    host = "wrong.example.com" if case == "wrong_hostname" else HOST
    if case != "untrusted_issuer":
        monkeypatch.setattr(certifi, "where", lambda: str(cert))

    async def dns(name):
        assert name == host
        return PUBLIC_IP

    connect = anyio.connect_tcp
    async def route(remote_host, remote_port, **kwargs):
        assert (remote_host, remote_port) == (PUBLIC_IP, 443)
        return await connect("127.0.0.1", server.sockets[0].getsockname()[1], **kwargs)

    monkeypatch.setattr(external, "resolve", dns)
    monkeypatch.setattr(anyio, "connect_tcp", route)
    async with server:
        if case == "matching":
            code, _, body = await external._read_pinned_get(**{**GET, "host": host})
            assert (code, body) == (200, b"{}")
            assert ("Host: " + host).lower().encode() in requests[0].lower()
        else:
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await external._read_pinned_get(**{**GET, "host": host})
            assert not requests
    assert names == [host]
