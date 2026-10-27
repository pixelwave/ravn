"""Client tests against a local TLS stub. They do not import Home Assistant."""

from __future__ import annotations

import hashlib
import importlib.util
import ssl
import subprocess
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "custom_components" / "ravn" / "client.py"


def load_client():
    source = CLIENT_PATH.read_text().replace("from .const import DEFAULT_PORT", "DEFAULT_PORT = 8770")
    spec = importlib.util.spec_from_loader("ravn_client_under_test", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(CLIENT_PATH), "exec"), module.__dict__)
    return module


client = load_client()


def write_cert(directory: Path) -> tuple[Path, Path, str]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.check_call(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=ravn",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


@pytest.fixture
async def agent(tmp_path: Path):
    cert, key, fingerprint = write_cert(tmp_path)
    state = {"sensors": {"cpu_temp": {"enabled": False, "interval_s": 30}}}

    async def identity(_request: web.Request) -> web.Response:
        return web.json_response(
            {"node_id": "node-1", "name": "example", "version": "26.10.27", "fingerprint": fingerprint}
        )

    async def readings(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "name": "example",
                "version": "26.10.27",
                "ip": {"primary": "192.0.2.10", "ipv4": ["192.0.2.10"], "ipv6": []},
                "catalog": [
                    {"id": "name", "enabled": True, "controllable": False},
                    {"id": "cpu_temp", "enabled": state["sensors"]["cpu_temp"]["enabled"], "controllable": True, "interval_s": 30},
                ],
                "readings": {},
                "update": {"latest": "", "notes": "", "configured": False},
            }
        )

    async def put_sensor(request: web.Request) -> web.Response:
        body = await request.json()
        if body["interval_s"] < 1 or body["interval_s"] > 86400:
            return web.json_response({"error": "invalid_sensor"}, status=400)
        state["sensors"]["cpu_temp"] = body
        return web.json_response({"ok": True})

    def check_token(request: web.Request) -> web.Response | None:
        if request.headers.get("Authorization") != "Bearer secret":
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    @web.middleware
    async def auth(request: web.Request, handler):
        denied = check_token(request)
        if denied is not None:
            return denied
        return await handler(request)

    app = web.Application(middlewares=[auth])
    app.router.add_get("/v1/identity", identity)
    app.router.add_get("/v1/readings", readings)
    app.router.add_put("/v1/sensors/{sensor_id}", put_sensor)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert, key)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield {"port": port, "fingerprint": fingerprint, "state": state}
    await runner.cleanup()


async def test_probe_and_rejected_token(agent):
    seen = await client.probe_fingerprint("127.0.0.1", agent["port"])
    assert seen == agent["fingerprint"]
    async with aiohttp.ClientSession() as session:
        api = client.RavnClient(session, "127.0.0.1", agent["port"], "nope", agent["fingerprint"])
        with pytest.raises(client.RavnAuthError):
            await api.identity()


async def test_pin_and_toggle(agent):
    async with aiohttp.ClientSession() as session:
        api = client.RavnClient(session, "127.0.0.1", agent["port"], "secret", agent["fingerprint"])
        identity = await api.identity()
        assert identity["name"] == "example"
        await api.put_sensor("cpu_temp", True, 15)
        assert agent["state"]["sensors"]["cpu_temp"]["enabled"] is True
        bad = client.RavnClient(session, "127.0.0.1", agent["port"], "secret", "ab" * 32)
        with pytest.raises(client.RavnError):
            await bad.identity()
