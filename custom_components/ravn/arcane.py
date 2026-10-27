"""HTTP client for one Arcane manager.

Home Assistant talks only to the manager URL. Agent addresses are used to name
machines. They are never opened.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

DEFAULT_ARCANE_PORT = 3552
PAGE_LIMIT = 100
MAX_PAGES = 50

# Docker renames a container "<12 hex>_<name>" while it is being recreated.
_RECREATE_NAME = re.compile(r"^[0-9a-f]{12}_")


class ArcaneError(Exception):
    """Base error for manager calls."""


class ArcaneAuthError(ArcaneError):
    """The manager rejected the API key."""


class ArcaneConnectError(ArcaneError):
    """The manager could not be reached."""


def normalize_url(value: str) -> str:
    """Return an origin with no path.

    A bare host, or http without a port, uses Arcane's port 3552. https without
    a port stays on 443 so a reverse proxy such as nginx is left alone. An
    explicit port is kept.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty url")
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host:
        raise ValueError("invalid url")
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    elif parts.scheme == "http":
        netloc = f"{host}:{DEFAULT_ARCANE_PORT}"
    else:
        netloc = host
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def manager_host(url: str) -> str:
    """Return the hostname from a normalized manager URL."""
    return urlsplit(url).hostname or ""


def _host_of(url: str) -> str:
    if not url:
        return ""
    return urlsplit(url).hostname or ""


def _local_environment(env: dict[str, Any]) -> bool:
    """The manager's own engine is environment 0, whose API URL is loopback."""
    if str(env.get("id")) == "0":
        return True
    return _host_of(str(env.get("apiUrl") or "")) in {"", "localhost", "127.0.0.1", "::1"}


def image_tag(image: str) -> str:
    """Return the tag from an image reference. A missing tag is Docker's `latest`."""
    ref = image.strip()
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    if ":" in name:
        return name.split(":", 1)[1] or "latest"
    return "latest" if ref else ""


def _short_digest(value: str) -> str:
    """Return the first 12 hex characters of an image digest."""
    raw = value.strip()
    if raw.startswith("sha256:"):
        raw = raw[7:]
    return raw[:12]


def _label_version(row: dict[str, Any]) -> str:
    """Return the software version baked into the image, when the image has one."""
    labels = row.get("labels")
    if not isinstance(labels, dict):
        return ""
    floating = {"latest", "unknown", "dev"}
    for key in ("org.opencontainers.image.version", "org.label-schema.version"):
        value = str(labels.get(key) or "").strip()
        if value and value.lower() not in floating:
            return value
    return ""


def container_versions(row: dict[str, Any]) -> dict[str, Any]:
    """Versions for one container row.

    The installed version prefers the image's version label, then a concrete
    tag. Arcane reports a real latest version when the tag moved. A digest
    update has no version number for the newer image, so that side stays the
    short digest and Home Assistant can still offer the update.
    """
    info = row.get("updateInfo")
    if not isinstance(info, dict):
        info = {}
    current = str(info.get("currentVersion") or "").strip()
    latest = str(info.get("latestVersion") or "").strip()
    floating = {"latest", "unknown"}
    labeled = _label_version(row)
    if labeled:
        installed = labeled
    elif current and current.lower() not in floating:
        installed = current
    else:
        installed = image_tag(str(row.get("image") or ""))
    checked = bool(str(info.get("checkTime") or "").strip()) or "hasUpdate" in info
    has_update = bool(info.get("hasUpdate"))
    error = str(info.get("error") or "").strip()
    if not checked:
        newest = None
    elif error and not has_update:
        newest = None
    elif not has_update:
        newest = installed or None
    elif latest and latest.lower() not in floating and latest != installed:
        newest = latest
    else:
        newest = _short_digest(str(info.get("latestDigest") or "")) or "update"
        if newest == installed:
            newest = "update"
    return {
        "installed": installed or None,
        "latest": newest,
        "has_update": has_update,
        "blocked": bool(row.get("redeployDisabled")),
        "checked": checked,
        "error": error,
        "update_type": str(info.get("updateType") or "").strip(),
    }


def container_name(row: dict[str, Any]) -> str:
    """Return the first container name without a leading slash."""
    names = row.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not names:
        single = row.get("name")
        names = [single] if single else []
    if not names or not names[0]:
        return ""
    return str(names[0]).lstrip("/")


def machine_title(name: str, ip: str) -> str:
    """Match the RAVN device title, `hostname • address`."""
    name = name.strip()
    ip = ip.strip()
    if name and ip and ip.lower() not in name.lower():
        return f"{name} • {ip}"
    return name or ip


def select_environments(environments: list[dict[str, Any]], include_remote: bool) -> list[dict[str, Any]]:
    """Keep the manager engine, plus remote engines only when asked."""
    enabled = [env for env in environments if env.get("enabled", True)]
    if include_remote:
        return enabled
    local = [env for env in enabled if _local_environment(env)]
    return local or [{"id": "0", "name": "", "apiUrl": "", "enabled": True}]


def assemble_snapshot(
    manager_url: str,
    environments: list[dict[str, Any]],
    infos: dict[str, dict[str, Any]],
    containers_by_env: dict[str, list[dict[str, Any]]],
    *,
    include_remote: bool,
) -> dict[str, Any]:
    """Group container rows under the machine that runs them."""
    host = manager_host(manager_url)
    machines: list[dict[str, Any]] = []
    for env in select_environments(environments, include_remote):
        env_id = str(env.get("id") or "0")
        info = infos.get(env_id) or {}
        docker_name = str(info.get("Name") or "")
        if not docker_name and isinstance(info.get("data"), dict):
            docker_name = str(info["data"].get("Name") or "")
        if _local_environment(env):
            ip = host
        else:
            ip = _host_of(str(env.get("apiUrl") or ""))
        name = docker_name or str(env.get("name") or "") or ip
        containers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in containers_by_env.get(env_id) or []:
            cname = container_name(row)
            if not cname or _RECREATE_NAME.match(cname):
                continue
            key = f"{env_id}:{cname}"
            if key in seen:
                suffix = str(row.get("id") or "")[:12]
                key = f"{key}:{suffix}" if suffix else key
            seen.add(key)
            containers.append(
                {
                    "key": key,
                    "env_id": env_id,
                    "name": cname,
                    "docker_id": str(row.get("id") or ""),
                    "state": str(row.get("state") or "unknown"),
                    "status": str(row.get("status") or ""),
                    "image": str(row.get("image") or ""),
                    "redeploy_disabled": bool(row.get("redeployDisabled")),
                    "update": container_versions(row),
                }
            )
        containers.sort(key=lambda item: item["name"].lower())
        machines.append(
            {
                "env_id": env_id,
                "name": name,
                "ip": ip,
                "title": machine_title(name, ip),
                "online": True,
                "containers": containers,
            }
        )
    machines.sort(key=lambda item: item["title"].lower())
    return {"url": manager_url, "machines": machines}


def merge_presence(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    missing_since: dict[str, float],
    *,
    now: float,
    grace_seconds: float | None,
    force_drop: bool,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Keep last data for a machine that did not answer, and delay real removals.

    A failed poll keeps that machine and marks it offline. Nothing is deleted
    while it is offline. A container that is gone from a machine that did answer
    stays until the grace period ends. ``grace_seconds`` of None never removes
    them. ``force_drop`` removes them immediately.
    """
    previous_machines = {
        str(machine.get("env_id") or ""): machine
        for machine in (previous or {}).get("machines") or []
    }
    current_machines = {
        str(machine.get("env_id") or ""): machine for machine in current.get("machines") or []
    }
    failures = {str(env_id) for env_id in current.get("failures") or []}
    missing = dict(missing_since)
    machines: list[dict[str, Any]] = []
    seen: set[str] = set()

    for env_id, machine in current_machines.items():
        seen.add(env_id)
        machines.append(
            _merge_online_machine(
                machine,
                previous_machines.get(env_id),
                missing,
                now=now,
                grace_seconds=grace_seconds,
                force_drop=force_drop,
            )
        )
    for env_id in failures:
        if env_id in seen:
            continue
        seen.add(env_id)
        kept = _offline_machine(previous_machines.get(env_id), missing)
        if kept is not None:
            machines.append(kept)
    for env_id, machine in previous_machines.items():
        if env_id in seen:
            continue
        kept = _merge_online_machine(
            {**machine, "containers": [], "online": True},
            machine,
            missing,
            now=now,
            grace_seconds=grace_seconds,
            force_drop=force_drop,
        )
        if kept["containers"]:
            machines.append(kept)

    live = {
        container["key"]
        for machine in machines
        for container in machine.get("containers") or []
        if container.get("missing")
    }
    missing = {key: stamp for key, stamp in missing.items() if key in live}
    merged = dict(current)
    merged["machines"] = sorted(machines, key=lambda item: str(item.get("title") or "").lower())
    return merged, missing


def drop_verified_missing(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Remove containers that are already known to be gone. Offline machines stay."""
    machines: list[dict[str, Any]] = []
    for machine in snapshot.get("machines") or []:
        item = dict(machine)
        if item.get("online") is False:
            machines.append(item)
            continue
        item["containers"] = [
            container for container in item.get("containers") or [] if not container.get("missing")
        ]
        item["online"] = True
        machines.append(item)
    result = dict(snapshot)
    result["machines"] = machines
    return result


def _offline_machine(
    machine: dict[str, Any] | None,
    missing: dict[str, float],
) -> dict[str, Any] | None:
    """Return the last containers for a machine that did not answer."""
    if machine is None:
        return None
    containers: list[dict[str, Any]] = []
    for container in machine.get("containers") or []:
        if _RECREATE_NAME.match(str(container.get("name") or "")):
            continue
        item = dict(container)
        item.pop("missing", None)
        missing.pop(str(item.get("key") or ""), None)
        containers.append(item)
    kept = dict(machine)
    kept["online"] = False
    kept["containers"] = containers
    return kept


def _merge_online_machine(
    machine: dict[str, Any],
    previous: dict[str, Any] | None,
    missing: dict[str, float],
    *,
    now: float,
    grace_seconds: float | None,
    force_drop: bool,
) -> dict[str, Any]:
    """Keep a container that this poll did not return until the grace period ends."""
    fresh = [dict(container) for container in machine.get("containers") or [] if not container.get("missing")]
    for container in fresh:
        container.pop("missing", None)
        missing.pop(str(container.get("key") or ""), None)
    current_keys = {str(container.get("key") or "") for container in fresh}
    previous_containers = {
        str(container.get("key") or ""): container
        for container in (previous or {}).get("containers") or []
    }
    for key, old in previous_containers.items():
        if not key or key in current_keys or _RECREATE_NAME.match(str(old.get("name") or "")):
            missing.pop(key, None)
            continue
        since = missing.get(key, now)
        if force_drop or (grace_seconds is not None and now - since >= grace_seconds):
            missing.pop(key, None)
            continue
        missing[key] = since
        ghost = dict(old)
        ghost["missing"] = True
        fresh.append(ghost)
    fresh.sort(key=lambda item: str(item.get("name") or "").lower())
    kept = dict(machine)
    kept["online"] = True
    kept["containers"] = fresh
    return kept


def match_parent(machine: dict[str, Any], nodes: list[dict[str, Any]]) -> tuple[str, bool]:
    """Return the device id for a machine, and whether that device is synthetic.

    A node matches when its stored host, reported name, or primary address equals
    the Arcane hostname or address. Otherwise the id is `arcane-machine:{env}`.
    """
    wanted = {str(machine.get("name") or "").strip().lower(), str(machine.get("ip") or "").strip().lower()}
    wanted.discard("")
    for node in nodes:
        keys = {
            str(node.get("host") or "").strip().lower(),
            str(node.get("name") or "").strip().lower(),
            str(node.get("ip") or "").strip().lower(),
        }
        keys.discard("")
        if keys & wanted:
            return str(node["node_id"]), False
    return f"arcane-machine:{machine['env_id']}", True


class ArcaneClient:
    """Small client over the shared Home Assistant aiohttp session."""

    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._api_key = api_key.strip()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                params=params,
                headers={"X-API-Key": self._api_key, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                text = await response.text()
                if response.status in {401, 403}:
                    raise ArcaneAuthError("API key was rejected")
                if response.status >= 400:
                    raise ArcaneError(f"manager returned {response.status}")
                if not text:
                    return {}
                payload = json_object(text)
        except ArcaneError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ArcaneConnectError("could not connect to the manager") from err
        return payload

    async def _pages(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """Read a start/limit collection until the manager reports no further rows."""
        rows: list[dict[str, Any]] = []
        start = 0
        for _ in range(MAX_PAGES):
            page = await self._request(
                "GET",
                path,
                params={**params, "start": str(start), "limit": str(PAGE_LIMIT)},
            )
            batch = page.get("data") or []
            if not isinstance(batch, list):
                break
            rows.extend(item for item in batch if isinstance(item, dict))
            pagination = page.get("pagination") or {}
            total = int(pagination.get("totalItems") or 0)
            start += PAGE_LIMIT
            if not batch or (total and start >= total) or len(batch) < PAGE_LIMIT:
                break
        return rows

    async def list_environments(self) -> list[dict[str, Any]]:
        """Return enabled and disabled environments. The caller filters them."""
        return await self._pages("/api/environments", {})

    async def list_containers(self, env_id: str) -> list[dict[str, Any]]:
        """Return containers for one environment, skipping internal and hidden rows."""
        return await self._pages(
            f"/api/environments/{quote(env_id, safe='')}/containers",
            {"includeInternal": "false", "includeHidden": "false"},
        )

    async def docker_info(self, env_id: str) -> dict[str, Any]:
        """Return the engine info the manager has for one environment."""
        return await self._request("GET", f"/api/environments/{quote(env_id, safe='')}/system/docker/info")

    async def action(self, env_id: str, docker_id: str, action: str, *, timeout: float) -> None:
        """Start, stop, or redeploy one container. Redeploy can pull an image."""
        if action not in {"start", "stop", "restart", "redeploy"}:
            raise ArcaneError("unknown container action")
        path = f"/api/environments/{quote(env_id, safe='')}/containers/{quote(docker_id, safe='')}/{action}"
        await self._request("POST", path, timeout=timeout)

    async def update_container(
        self,
        env_id: str,
        docker_id: str,
        *,
        timeout: float = 600,
        poll: float = 2,
    ) -> None:
        """Pull the newer image and recreate the container.

        Arcane runs this as an activity. This waits until that activity finishes.
        It does not ask the registry itself. The list poll already carries the check.
        """
        path = f"/api/environments/{quote(env_id, safe='')}/containers/{quote(docker_id, safe='')}/update"
        payload = await self._request("POST", path, params={"async": "true"}, timeout=60)
        activity = _activity_record(payload)
        activity_id = str(activity.get("id") or "")
        if not activity_id:
            return
        deadline = asyncio.get_running_loop().time() + timeout
        while str(activity.get("status") or "") not in {"success", "failed", "cancelled"}:
            if asyncio.get_running_loop().time() >= deadline:
                raise ArcaneError("container update did not finish")
            await asyncio.sleep(poll)
            detail = await self._request(
                "GET",
                f"/api/environments/{quote(env_id, safe='')}/activities/{quote(activity_id, safe='')}",
            )
            activity = _activity_record(detail)
        status = str(activity.get("status") or "")
        if status == "failed":
            message = str(activity.get("error") or activity.get("latestMessage") or "container update failed")
            raise ArcaneError(message)
        if status == "cancelled":
            raise ArcaneError("container update was cancelled")

    async def check(self, *, include_remote: bool) -> None:
        """Confirm the key can read the manager. One page is enough."""
        await self._request(
            "GET",
            "/api/environments/0/containers",
            params={"start": "0", "limit": "1", "includeInternal": "false", "includeHidden": "false"},
        )
        if include_remote:
            await self._request("GET", "/api/environments", params={"start": "0", "limit": "1"})

    async def snapshot(self, *, include_remote: bool) -> dict[str, Any]:
        """Read the machines this manager should show.

        A remote environment that does not answer is left out. The manager's own
        engine is required when remote environments are off. An authentication
        failure always propagates.
        """
        if include_remote:
            environments = await self.list_environments()
        else:
            environments = [{"id": "0", "name": "", "apiUrl": "", "enabled": True}]
        selected = select_environments(environments, include_remote)
        infos: dict[str, dict[str, Any]] = {}
        containers: dict[str, list[dict[str, Any]]] = {}
        failed: set[str] = set()
        for env in selected:
            env_id = str(env.get("id") or "0")
            try:
                try:
                    infos[env_id] = await self.docker_info(env_id)
                except ArcaneAuthError:
                    raise
                except ArcaneError:
                    infos[env_id] = {}
                containers[env_id] = await self.list_containers(env_id)
            except ArcaneAuthError:
                raise
            except ArcaneError:
                failed.add(env_id)
                if not include_remote:
                    raise
        readable = [env for env in selected if str(env.get("id") or "0") not in failed]
        snapshot = assemble_snapshot(
            self.url,
            readable,
            infos,
            containers,
            include_remote=include_remote,
        )
        snapshot["failures"] = sorted(failed)
        if not snapshot["machines"] and failed:
            raise ArcaneConnectError("could not read containers")
        return snapshot


def _activity_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the activity object from a wrapped or bare manager response."""
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("activity")
        if isinstance(nested, dict):
            return nested
        if "id" in data or "status" in data:
            return data
    nested = payload.get("activity")
    if isinstance(nested, dict):
        return nested
    return payload


def json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object. A non-object is an unexpected payload."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        raise ArcaneError("manager returned an unexpected payload") from err
    if not isinstance(payload, dict):
        raise ArcaneError("manager returned an unexpected payload")
    return payload
