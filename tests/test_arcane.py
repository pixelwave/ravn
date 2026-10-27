"""Arcane client tests. They do not import Home Assistant or use a real API key."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
ARCANE_PATH = ROOT / "custom_components" / "ravn" / "arcane.py"
CONST_PATH = ROOT / "custom_components" / "ravn" / "const.py"


def load_arcane():
    spec = importlib.util.spec_from_loader("arcane_under_test", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(ARCANE_PATH.read_text(), str(ARCANE_PATH), "exec"), module.__dict__)
    return module


def load_const():
    spec = importlib.util.spec_from_loader("const_under_test", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(CONST_PATH.read_text(), str(CONST_PATH), "exec"), module.__dict__)
    return module


arcane = load_arcane()
const = load_const()


def test_entity_ids_start_with_ravn():
    assert const.ravn_entity_id("sensor", "bazarr", "Status") == "sensor.ravn_bazarr_status"
    assert (
        const.ravn_entity_id("switch", "gnomeregan", "Disk write rate")
        == "switch.ravn_gnomeregan_disk_write_rate"
    )
    assert const.ravn_entity_id("update", "area52", "Agent") == "update.ravn_area52_agent"


def test_normalize_url_adds_scheme_and_port():
    assert arcane.normalize_url("192.0.2.10") == "http://192.0.2.10:3552"
    assert arcane.normalize_url("http://192.0.2.10:3552/") == "http://192.0.2.10:3552"
    assert arcane.normalize_url("https://arcane.example") == "https://arcane.example"
    assert arcane.normalize_url("https://arcane.example:8443/api") == "https://arcane.example:8443"


def test_local_machine_uses_manager_host_not_loopback():
    snapshot = arcane.assemble_snapshot(
        "http://192.0.2.10:3552",
        [{"id": "0", "name": "Dalaran", "apiUrl": "http://localhost:3552", "enabled": True}],
        {"0": {"Name": "dalaran"}},
        {
            "0": [
                {
                    "id": "abc",
                    "names": ["/adguard"],
                    "state": "running",
                    "status": "Up 1 hour",
                    "image": "adguard/adguardhome:latest",
                    "redeployDisabled": False,
                }
            ]
        },
        include_remote=False,
    )
    machine = snapshot["machines"][0]
    assert machine["title"] == "dalaran • 192.0.2.10"
    assert machine["containers"][0]["name"] == "adguard"
    assert machine["containers"][0]["key"] == "0:adguard"


def test_remote_machines_are_separate_and_optional():
    environments = [
        {"id": "0", "name": "Dalaran", "apiUrl": "http://localhost:3552", "enabled": True},
        {"id": "env-2", "name": "Area 52", "apiUrl": "http://192.0.2.18:3553", "enabled": True},
    ]
    infos = {"0": {"Name": "dalaran"}, "env-2": {"Name": "area52"}}
    containers = {
        "0": [{"id": "1", "names": ["arcane"], "state": "running", "status": "Up", "image": "arcane"}],
        "env-2": [{"id": "2", "names": ["nginx"], "state": "exited", "status": "Exited", "image": "nginx"}],
    }
    local = arcane.assemble_snapshot(
        "http://192.0.2.10:3552", environments, infos, containers, include_remote=False
    )
    assert [machine["env_id"] for machine in local["machines"]] == ["0"]
    remote = arcane.assemble_snapshot(
        "http://192.0.2.10:3552", environments, infos, containers, include_remote=True
    )
    titles = [machine["title"] for machine in remote["machines"]]
    assert "area52 • 192.0.2.18" in titles
    assert "dalaran • 192.0.2.10" in titles


def test_container_versions_use_tags_and_digests():
    pinned = arcane.container_versions(
        {
            "image": "library/nginx:1.27",
            "updateInfo": {
                "checkTime": "2026-10-27T14:00:00Z",
                "updateType": "version",
                "currentVersion": "1.27",
                "latestVersion": "1.28",
                "hasUpdate": True,
            },
        }
    )
    assert pinned["installed"] == "1.27"
    assert pinned["latest"] == "1.28"
    floating = arcane.container_versions(
        {
            "image": "docker.io/adguard/adguardhome:latest",
            "updateInfo": {
                "checkTime": "2026-10-27T14:00:00Z",
                "updateType": "digest",
                "currentVersion": "latest",
                "latestVersion": "",
                "currentDigest": "sha256:aaa",
                "latestDigest": "sha256:aaa",
                "hasUpdate": False,
            },
        }
    )
    assert floating["installed"] == "latest"
    assert floating["latest"] == "latest"
    digest = arcane.container_versions(
        {
            "image": "ghcr.io/getarcaneapp/manager:latest",
            "redeployDisabled": True,
            "updateInfo": {
                "checkTime": "2026-10-27T14:00:00Z",
                "updateType": "digest",
                "currentVersion": "latest",
                "latestVersion": "",
                "latestDigest": "sha256:3d7057b7e3b3e0be4b3ba34d",
                "hasUpdate": True,
            },
        }
    )
    assert digest["installed"] == "latest"
    assert digest["latest"] == "3d7057b7e3b3"
    assert digest["blocked"] is True
    labeled = arcane.container_versions(
        {
            "image": "ghcr.io/getarcaneapp/manager:latest",
            "labels": {"org.opencontainers.image.version": "v2.16.1"},
            "updateInfo": {
                "checkTime": "2026-10-27T14:00:00Z",
                "updateType": "digest",
                "currentVersion": "latest",
                "latestVersion": "",
                "latestDigest": "sha256:3d7057b7e3b3e0be4b3ba34d",
                "hasUpdate": False,
            },
        }
    )
    assert labeled["installed"] == "v2.16.1"
    assert labeled["latest"] == "v2.16.1"
    unchecked = arcane.container_versions({"image": "nginx:1.27"})
    assert unchecked["installed"] == "1.27"
    assert unchecked["latest"] is None


def _machine(env_id: str, *names: str) -> dict:
    return {
        "env_id": env_id,
        "name": env_id,
        "title": env_id,
        "ip": "192.0.2.10",
        "online": True,
        "containers": [
            {
                "key": f"{env_id}:{name}",
                "env_id": env_id,
                "name": name,
                "docker_id": name,
                "state": "running",
                "status": "Up",
                "image": name,
                "redeploy_disabled": False,
            }
            for name in names
        ],
    }


def test_recreate_placeholder_is_skipped_and_machine_is_online():
    snapshot = arcane.assemble_snapshot(
        "http://192.0.2.10:3552",
        [{"id": "0", "name": "Dalaran", "apiUrl": "http://localhost:3552", "enabled": True}],
        {"0": {"Name": "dalaran"}},
        {
            "0": [
                {"id": "tmp", "names": ["/a1b2c3d4e5f6_nginx"], "state": "running", "image": "nginx"},
                {"id": "real", "names": ["/nginx"], "state": "running", "image": "nginx"},
            ]
        },
        include_remote=False,
    )
    machine = snapshot["machines"][0]
    assert machine["online"] is True
    assert [item["name"] for item in machine["containers"]] == ["nginx"]


def test_offline_machine_is_kept_until_it_answers():
    previous = {"machines": [_machine("0", "adguard"), _machine("env-2", "bazarr")]}
    current = {"url": "http://192.0.2.10:3552", "machines": [_machine("0", "adguard")], "failures": ["env-2"]}
    merged, missing = arcane.merge_presence(
        previous, current, {}, now=1_000, grace_seconds=86_400, force_drop=False
    )
    offline = next(machine for machine in merged["machines"] if machine["env_id"] == "env-2")
    assert offline["online"] is False
    assert [item["name"] for item in offline["containers"]] == ["bazarr"]
    assert missing == {}
    cleaned = arcane.drop_verified_missing(merged)
    assert any(machine["env_id"] == "env-2" for machine in cleaned["machines"])


def test_vanished_container_waits_out_the_grace_period():
    previous = {"machines": [_machine("0", "adguard", "nginx")]}
    current = {"machines": [_machine("0", "adguard")], "failures": []}
    merged, missing = arcane.merge_presence(
        previous, current, {}, now=1_000, grace_seconds=3_600, force_drop=False
    )
    nginx = next(item for item in merged["machines"][0]["containers"] if item["name"] == "nginx")
    assert nginx["missing"] is True
    assert missing["0:nginx"] == 1_000
    still, missing = arcane.merge_presence(
        previous, current, missing, now=4_000, grace_seconds=3_600, force_drop=False
    )
    assert "nginx" in {item["name"] for item in still["machines"][0]["containers"]}
    gone, missing = arcane.merge_presence(
        previous, current, missing, now=5_000, grace_seconds=3_600, force_drop=False
    )
    assert "nginx" not in {item["name"] for item in gone["machines"][0]["containers"]}
    assert "0:nginx" not in missing


def test_zero_grace_keeps_a_missing_container_until_cleanup():
    previous = {"machines": [_machine("0", "adguard", "nginx")]}
    current = {"machines": [_machine("0", "adguard")], "failures": []}
    merged, missing = arcane.merge_presence(
        previous, current, {}, now=1_000, grace_seconds=None, force_drop=False
    )
    later, missing = arcane.merge_presence(
        previous, current, missing, now=1_000_000, grace_seconds=None, force_drop=False
    )
    assert "nginx" in {item["name"] for item in later["machines"][0]["containers"]}
    cleaned = arcane.drop_verified_missing(later)
    assert "nginx" not in {item["name"] for item in cleaned["machines"][0]["containers"]}
    assert missing["0:nginx"] == 1_000


def test_failed_poll_pauses_a_missing_container():
    previous = {"machines": [_machine("0", "adguard", "nginx")]}
    current = {"machines": [_machine("0", "adguard")], "failures": []}
    merged, missing = arcane.merge_presence(
        previous, current, {}, now=1_000, grace_seconds=3_600, force_drop=False
    )
    failed = {"machines": [], "failures": ["0"]}
    held, missing = arcane.merge_presence(
        merged, failed, missing, now=9_000, grace_seconds=3_600, force_drop=False
    )
    machine = held["machines"][0]
    assert machine["online"] is False
    nginx = next(item for item in machine["containers"] if item["name"] == "nginx")
    assert "missing" not in nginx
    assert missing == {}


def test_match_parent_prefers_a_ravn_node():
    machine = {"env_id": "0", "name": "dalaran", "ip": "192.0.2.10"}
    nodes = [{"node_id": "node-1", "host": "dalaran.local", "name": "dalaran", "ip": "192.0.2.10"}]
    assert arcane.match_parent(machine, nodes) == ("node-1", False)
    assert arcane.match_parent(machine, []) == ("arcane-machine:0", True)


@pytest.fixture
async def manager():
    state = {"containers": [{"id": "c1", "names": ["nginx"], "state": "running", "status": "Up", "image": "nginx"}]}

    def authorized(request: web.Request) -> None:
        if request.headers.get("X-API-Key") != "arc_test":
            raise web.HTTPUnauthorized(text="no")

    async def containers(request: web.Request) -> web.Response:
        authorized(request)
        start = int(request.query.get("start", "0"))
        limit = int(request.query.get("limit", "100"))
        rows = state["containers"]
        return web.json_response(
            {
                "success": True,
                "data": rows[start : start + limit],
                "pagination": {"totalItems": len(rows), "totalPages": 1, "currentPage": 1, "itemsPerPage": limit},
            }
        )

    async def info(request: web.Request) -> web.Response:
        authorized(request)
        return web.json_response({"Name": "dalaran"})

    async def environments(request: web.Request) -> web.Response:
        authorized(request)
        data = [
            {"id": "0", "name": "Dalaran", "apiUrl": "http://localhost:3552", "enabled": True},
            {"id": "env-2", "name": "Area 52", "apiUrl": "http://192.0.2.18:3553", "enabled": True},
        ]
        return web.json_response({"success": True, "data": data, "pagination": {"totalItems": len(data)}})

    async def action(request: web.Request) -> web.Response:
        authorized(request)
        state["last_action"] = (request.match_info["container_id"], request.path.rsplit("/", 1)[-1])
        return web.json_response({"success": True, "data": {"message": "ok"}})

    app = web.Application()
    app.router.add_get("/api/environments/0/containers", containers)
    app.router.add_get("/api/environments/env-2/containers", containers)
    app.router.add_get("/api/environments/0/system/docker/info", info)
    app.router.add_get("/api/environments/env-2/system/docker/info", info)
    app.router.add_get("/api/environments", environments)
    app.router.add_post("/api/environments/{env_id}/containers/{container_id}/start", action)
    app.router.add_post("/api/environments/{env_id}/containers/{container_id}/stop", action)
    app.router.add_post("/api/environments/{env_id}/containers/{container_id}/restart", action)
    app.router.add_post("/api/environments/{env_id}/containers/{container_id}/redeploy", action)

    async def update(request: web.Request) -> web.Response:
        authorized(request)
        state["update_async"] = request.query.get("async")
        state["update_target"] = (request.match_info["env_id"], request.match_info["container_id"])
        state["polls"] = 0
        return web.json_response({"success": True, "data": {"id": "act-1", "status": "running"}})

    async def activity(request: web.Request) -> web.Response:
        authorized(request)
        state["polls"] = state.get("polls", 0) + 1
        status = "failed" if state.get("fail_update") else "success" if state["polls"] >= 2 else "running"
        return web.json_response(
            {
                "success": True,
                "data": {"activity": {"id": "act-1", "status": status, "error": "pull failed"}},
            }
        )

    app.router.add_post("/api/environments/{env_id}/containers/{container_id}/update", update)
    app.router.add_get("/api/environments/{env_id}/activities/{activity_id}", activity)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_snapshot_sends_the_api_key_and_pages(manager):
    url, state = manager
    state["containers"] = [
        {"id": "c1", "names": ["one"], "state": "running", "status": "Up", "image": "one"},
        {"id": "c2", "names": ["two"], "state": "exited", "status": "Exited", "image": "two"},
    ]
    original = arcane.PAGE_LIMIT
    arcane.PAGE_LIMIT = 1
    try:
        async with aiohttp.ClientSession() as session:
            client = arcane.ArcaneClient(session, url, "arc_test")
            snapshot = await client.snapshot(include_remote=False)
    finally:
        arcane.PAGE_LIMIT = original
    names = [item["name"] for item in snapshot["machines"][0]["containers"]]
    assert names == ["one", "two"]
    assert snapshot["machines"][0]["title"] == "dalaran • 127.0.0.1"


@pytest.mark.asyncio
async def test_rejected_key_and_actions(manager):
    url, state = manager
    async with aiohttp.ClientSession() as session:
        client = arcane.ArcaneClient(session, url, "nope")
        with pytest.raises(arcane.ArcaneAuthError):
            await client.check(include_remote=False)
        client = arcane.ArcaneClient(session, url, "arc_test")
        await client.action("0", "c1", "stop", timeout=5)
        await client.action("0", "c1", "start", timeout=5)
        await client.action("0", "c1", "restart", timeout=5)
        await client.action("0", "c1", "redeploy", timeout=5)
        await client.update_container("0", "c1", timeout=5, poll=0)
    assert state["last_action"] == ("c1", "redeploy")
    assert state["update_async"] == "true"
    assert state["update_target"] == ("0", "c1")
    assert state["polls"] == 2


@pytest.mark.asyncio
async def test_container_update_reports_a_failed_activity(manager):
    url, state = manager
    state["fail_update"] = True
    async with aiohttp.ClientSession() as session:
        client = arcane.ArcaneClient(session, url, "arc_test")
        with pytest.raises(arcane.ArcaneError, match="pull failed"):
            await client.update_container("0", "c1", timeout=5, poll=0)


@pytest.mark.asyncio
async def test_remote_toggle_controls_environment_list(manager):
    url, _state = manager
    async with aiohttp.ClientSession() as session:
        client = arcane.ArcaneClient(session, url, "arc_test")
        local = await client.snapshot(include_remote=False)
        both = await client.snapshot(include_remote=True)
    assert [machine["env_id"] for machine in local["machines"]] == ["0"]
    assert {machine["env_id"] for machine in both["machines"]} == {"0", "env-2"}
