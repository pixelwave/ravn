"""The RAVN integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .arcane import ArcaneClient, match_parent
from .client import RavnClient
from .const import (
    CONF_ADOPT_FROM,
    CONF_ARCANE,
    CONF_ARCANE_ENV,
    CONF_ARCANE_MANAGER,
    CONF_AUTO_TITLE,
    CONF_FINGERPRINT,
    CONF_MACHINE_IP,
    CONF_MACHINE_NAME,
    CONF_NODE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .containers import node_matches, sync_arcane_devices
from .coordinator import ArcaneCoordinator, RavnCoordinator
from .nodes import (
    HUB_TITLE,
    HUB_UNIQUE_ID,
    entry_nodes,
    entry_payload,
    find_entry_for_machine,
    machine_keys,
)

_LOGGER = logging.getLogger(__name__)
_SETUP_LOCK = f"{DOMAIN}_setup_lock"

type RavnConfigEntry = ConfigEntry[RavnData]


@dataclass
class RavnData:
    """Runtime objects for the nodes and, when configured, the Arcane manager."""

    coordinators: dict[str, RavnCoordinator]
    arcane: ArcaneCoordinator | None = None


async def async_setup_entry(hass: HomeAssistant, entry: RavnConfigEntry) -> bool:
    """Set up one machine."""
    if not await _split_hub(hass, entry):
        return False
    entry = hass.config_entries.async_get_entry(entry.entry_id) or entry
    _adopt_node(hass, entry)
    entry = hass.config_entries.async_get_entry(entry.entry_id) or entry

    coordinators: dict[str, RavnCoordinator] = {}
    for node in entry_nodes(entry.data):
        client = RavnClient(
            async_get_clientsession(hass),
            node["host"],
            int(node["port"]),
            node["token"],
            node[CONF_FINGERPRINT],
        )
        coordinator = RavnCoordinator(hass, entry, client, node)
        try:
            await coordinator.async_config_entry_first_refresh()
        except (ConfigEntryNotReady, ConfigEntryAuthFailed) as err:
            _LOGGER.warning("RAVN node %s is not ready: %s", node["host"], err.__cause__ or err)
        coordinators[coordinator.node_id] = coordinator
        _remember_agent(hass, entry, coordinator)

    owns_arcane = _stores_arcane(entry)
    arcane = _arcane_coordinator(hass, entry) if owns_arcane else _shared_arcane(hass)
    if arcane is not None and owns_arcane:
        try:
            await arcane.async_config_entry_first_refresh()
        except (ConfigEntryNotReady, ConfigEntryAuthFailed) as err:
            _LOGGER.warning("Arcane manager is not ready: %s", err.__cause__ or err)
        else:
            try:
                await _ensure_machine_entries(hass, entry, arcane, coordinators)
            except Exception:
                _LOGGER.exception("Could not add one entry per Arcane machine")
            entry = hass.config_entries.async_get_entry(entry.entry_id) or entry
    entry.runtime_data = RavnData(coordinators, arcane)

    def _sync_devices() -> None:
        current = hass.config_entries.async_get_entry(entry.entry_id) or entry
        sync_arcane_devices(hass, current, arcane, coordinators)
        for coordinator in coordinators.values():
            _remember_agent(hass, current, coordinator)

    if arcane is not None:
        entry.async_on_unload(arcane.async_add_listener(_sync_devices))
        if owns_arcane:
            entry.async_on_unload(arcane.async_shutdown)
            entry.async_on_unload(arcane.async_add_listener(lambda: _schedule_ensure(hass, entry)))
    for coordinator in coordinators.values():
        entry.async_on_unload(coordinator.async_add_listener(_sync_devices))
    _sync_devices()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if owns_arcane:
        for other in hass.config_entries.async_entries(DOMAIN):
            if other.entry_id == entry.entry_id or other.state is not ConfigEntryState.LOADED:
                continue
            if other.data.get(CONF_ARCANE_MANAGER) == entry.entry_id:
                hass.config_entries.async_schedule_reload(other.entry_id)
    return True


def _stores_arcane(entry: ConfigEntry) -> bool:
    """This entry holds the manager URL and API key."""
    raw = entry.data.get(CONF_ARCANE)
    return isinstance(raw, dict) and bool(raw.get("url")) and bool(raw.get("api_key"))


def _shared_arcane(hass: HomeAssistant) -> ArcaneCoordinator | None:
    """Return the one manager poller, if the entry that owns it is loaded."""
    found = hass.data.get(DOMAIN, {}).get("arcane")
    return found if isinstance(found, ArcaneCoordinator) else None


def _arcane_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> ArcaneCoordinator | None:
    """Build the manager poller when the entry stores a URL and an API key."""
    raw = entry.data.get(CONF_ARCANE)
    if not isinstance(raw, dict) or not raw.get("url") or not raw.get("api_key"):
        return None
    client = ArcaneClient(async_get_clientsession(hass), str(raw["url"]), str(raw["api_key"]))
    interval = int(raw.get("scan_interval") or DEFAULT_SCAN_INTERVAL)
    coordinator = ArcaneCoordinator(hass, entry, client, interval, bool(raw.get("include_remote", True)))
    bucket = hass.data.setdefault(DOMAIN, {})
    bucket["arcane"] = coordinator
    bucket["arcane_entry"] = entry.entry_id
    return coordinator


def _remember_agent(hass: HomeAssistant, entry: ConfigEntry, coordinator: RavnCoordinator) -> None:
    """Store the name and address the agent reported, for matching Arcane later."""
    data = coordinator.data or {}
    name = str(data.get("name") or "").strip()
    ip = str((data.get("ip") or {}).get("primary") or "").strip()
    if not name and not ip:
        return
    current = hass.config_entries.async_get_entry(entry.entry_id) or entry
    payload = dict(current.data)
    if (not name or payload.get(CONF_MACHINE_NAME)) and (not ip or payload.get(CONF_MACHINE_IP)):
        return
    if name and not payload.get(CONF_MACHINE_NAME):
        payload[CONF_MACHINE_NAME] = name
    if ip and not payload.get(CONF_MACHINE_IP):
        payload[CONF_MACHINE_IP] = ip
    hass.config_entries.async_update_entry(current, data=entry_payload(payload, entry_nodes(payload)))


def _schedule_ensure(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Look for newly reported machines without blocking the poll callback."""
    hass.async_create_task(_ensure_from_listener(hass, entry.entry_id))


async def _ensure_from_listener(hass: HomeAssistant, entry_id: str) -> None:
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime = getattr(entry, "runtime_data", None) if entry is not None else None
    arcane = getattr(runtime, "arcane", None) if runtime is not None else None
    coordinators = getattr(runtime, "coordinators", {}) if runtime is not None else {}
    if entry is None or arcane is None or not _stores_arcane(entry):
        return
    await _ensure_machine_entries(hass, entry, arcane, coordinators)


async def _split_hub(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Turn the old Local Network entry into one entry per machine.

    Returns false when this entry was removed because nothing was left on it.
    """
    nodes = entry_nodes(entry.data)
    bundled = entry.unique_id == HUB_UNIQUE_ID and len(nodes) >= 1 or len(nodes) > 1
    if not bundled:
        return True
    _LOGGER.info("Splitting Local Network into one entry per machine")
    _drop_arcane_registry(hass, entry)
    arcane = entry.data.get(CONF_ARCANE) if _stores_arcane(entry) else None
    hass.config_entries.async_update_entry(entry, data=entry_payload({CONF_ARCANE: arcane} if arcane else {}, []))
    for node in nodes:
        device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, node[CONF_NODE_ID]), entry.entry_id)
        title = ""
        if device is not None:
            title = device.name_by_user or device.name or ""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "machine", "unique_id": node[CONF_NODE_ID]},
            data={
                "node": node,
                "title": title or node["host"],
                CONF_AUTO_TITLE: title or node["host"],
                CONF_ADOPT_FROM: entry.entry_id,
                CONF_MACHINE_NAME: title.split(" • ")[0] if title else "",
                CONF_MACHINE_IP: node["host"],
            },
        )
    if arcane:
        return True
    await hass.config_entries.async_remove(entry.entry_id)
    return False


def _adopt_node(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an agent device onto the entry that now owns it."""
    source_id = entry.data.get(CONF_ADOPT_FROM)
    nodes = entry_nodes(entry.data)
    if not source_id or len(nodes) != 1:
        return
    node_id = nodes[0][CONF_NODE_ID]
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, node_id), str(source_id))
    if device is not None and not dr.async_entries_for_parent_device(devices, device.id):
        devices.async_update_device(device.id, new_config_entry_id=entry.entry_id)
    prefix = f"{node_id}_"
    for item in list(er.async_entries_for_config_entry(entities, str(source_id))):
        if item.unique_id.startswith(prefix) or (device is not None and item.device_id == device.id):
            entities.async_update_entity(item.entity_id, config_entry_id=entry.entry_id)
    payload = dict(entry.data)
    payload.pop(CONF_ADOPT_FROM, None)
    hass.config_entries.async_update_entry(entry, data=entry_payload(payload, nodes))


def _drop_arcane_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove container devices so each machine can create its own again."""
    devices = dr.async_get(hass)
    for child in list(dr.async_child_entries_for_config_entry(devices, entry.entry_id)):
        devices.async_remove_device(child.id)
    for device in list(dr.async_entries_for_config_entry(devices, entry.entry_id)):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and (identifier.startswith("arcane-container:") or identifier.startswith("arcane-machine:")):
                devices.async_remove_device(device.id)
                break
    registry = er.async_get(hass)
    for item in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if item.unique_id.startswith("arcane:"):
            registry.async_remove(item.entity_id)


def _own_machine(
    entry: ConfigEntry,
    snapshot: dict[str, Any],
    coordinators: dict[str, Any],
) -> dict[str, Any] | None:
    """Pick the snapshot machine this entry represents."""
    machines = list(snapshot.get("machines") or [])
    env_id = str(entry.data.get(CONF_ARCANE_ENV) or "")
    if env_id:
        for machine in machines:
            if str(machine.get("env_id") or "") == env_id:
                return machine
    nodes = node_matches(entry, coordinators)
    for machine in machines:
        _parent, synthetic = match_parent(machine, nodes)
        if nodes and not synthetic:
            return machine
    keys = machine_keys(entry)
    for machine in machines:
        wanted = {str(machine.get("name") or "").strip().lower(), str(machine.get("ip") or "").strip().lower()}
        wanted.discard("")
        if keys & wanted:
            return machine
    for machine in machines:
        if str(machine.get("env_id") or "") == "0":
            return machine
    return machines[0] if machines else None


def _link_machine(hass: HomeAssistant, entry: ConfigEntry, manager: ConfigEntry, machine: dict[str, Any]) -> None:
    """Point an entry at one environment and title it hostname • address."""
    current = hass.config_entries.async_get_entry(entry.entry_id) or entry
    payload = dict(current.data)
    payload[CONF_ARCANE_ENV] = str(machine.get("env_id") or "")
    if current.entry_id != manager.entry_id:
        payload[CONF_ARCANE_MANAGER] = manager.entry_id
    payload[CONF_MACHINE_NAME] = str(machine.get("name") or "")
    payload[CONF_MACHINE_IP] = str(machine.get("ip") or "")
    title = str(machine.get("title") or current.title)
    auto = str(payload.get(CONF_AUTO_TITLE) or "")
    rename = current.title in {HUB_TITLE, auto, str(payload.get(CONF_MACHINE_IP) or ""), str(payload.get(CONF_MACHINE_NAME) or "")} or not auto
    if rename:
        payload[CONF_AUTO_TITLE] = title
    stored = entry_payload(payload, entry_nodes(payload))
    same = stored == entry_payload(dict(current.data), entry_nodes(current.data)) and (not rename or current.title == title)
    if same:
        return
    hass.config_entries.async_update_entry(current, title=title if rename else current.title, data=stored)
    if current.entry_id != manager.entry_id and current.state is ConfigEntryState.LOADED:
        hass.config_entries.async_schedule_reload(current.entry_id)


async def _ensure_machine_entries(
    hass: HomeAssistant,
    manager: ConfigEntry,
    arcane: ArcaneCoordinator,
    coordinators: dict[str, Any],
) -> None:
    """Create a collapsible entry for every machine the manager reports."""
    snapshot = arcane.data or {}
    if not snapshot.get("machines"):
        return
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault("ensure_lock", asyncio.Lock())
    async with lock:
        manager = hass.config_entries.async_get_entry(manager.entry_id) or manager
        own = _own_machine(manager, snapshot, coordinators)
        if own is not None:
            _link_machine(hass, manager, manager, own)
        for machine in snapshot.get("machines") or []:
            if own is not None and str(machine.get("env_id")) == str(own.get("env_id")):
                continue
            if find_entry_for_machine(hass, machine) is not None:
                found = find_entry_for_machine(hass, machine)
                if found is not None and found.entry_id != manager.entry_id:
                    _link_machine(hass, found, manager, machine)
                continue
            env_id = str(machine.get("env_id") or "")
            unique_id = f"arcane:{env_id}"
            if any(
                flow.get("context", {}).get("unique_id") == unique_id
                for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            ):
                continue
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "machine", "unique_id": unique_id},
                data={
                    "title": machine.get("title") or env_id,
                    CONF_ARCANE_ENV: env_id,
                    CONF_ARCANE_MANAGER: manager.entry_id,
                    CONF_MACHINE_NAME: machine.get("name") or "",
                    CONF_MACHINE_IP: machine.get("ip") or "",
                    CONF_AUTO_TITLE: machine.get("title") or "",
                },
            )


async def async_unload_entry(hass: HomeAssistant, entry: RavnConfigEntry) -> bool:
    """Unload one machine."""
    bucket = hass.data.get(DOMAIN) or {}
    if bucket.get("arcane_entry") == entry.entry_id:
        bucket.pop("arcane", None)
        bucket.pop("arcane_entry", None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Deleting the machine device removes that machine's entry."""
    identifier = next(
        (value for domain, value in device_entry.identifiers if domain == DOMAIN),
        None,
    )
    if identifier is None or identifier.startswith("arcane-container:"):
        return False
    hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
    return True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """YAML setup is not used."""
    hass.data.setdefault(_SETUP_LOCK, asyncio.Lock())
    return True
