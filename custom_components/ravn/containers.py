"""Arcane container devices under each machine."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .arcane import ArcaneError, match_parent
from .const import CONF_ARCANE_ENV, DOMAIN, ravn_entity_id
from .coordinator import ArcaneCoordinator
from .nodes import entry_nodes

_LOGGER = logging.getLogger(__name__)

CONTAINER_ACTIONS = ("restart", "redeploy")


def node_matches(entry: ConfigEntry, coordinators: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stored nodes plus the name and address the agent last reported."""
    rows: list[dict[str, Any]] = []
    for node in entry_nodes(entry.data):
        data = {}
        coordinator = coordinators.get(node["node_id"])
        if coordinator is not None and coordinator.data:
            data = coordinator.data
        ip = data.get("ip") or {}
        rows.append(
            {
                "node_id": node["node_id"],
                "host": node["host"],
                "name": data.get("name") or "",
                "ip": ip.get("primary") or "",
            }
        )
    return rows


def iter_containers(data: dict[str, Any] | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return (machine, container) pairs from a snapshot."""
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for machine in (data or {}).get("machines") or []:
        for container in machine.get("containers") or []:
            found.append((machine, container))
    return found


def container_entity_stem(data: dict[str, Any] | None, key: str) -> str:
    """Name used in the entity id.

    A container name that exists on more than one machine includes that machine,
    so the ids stay distinct. A unique name stays as the container name.
    """
    pair = container_by_key(data).get(key)
    if pair is None:
        return key
    machine, container = pair
    name = str(container.get("name") or key)
    copies = [
        item
        for _machine, item in iter_containers(data)
        if str(item.get("name") or "") == name
    ]
    if len(copies) > 1:
        return f"{machine.get('name') or machine.get('title') or ''} {name}".strip()
    return name


def container_entity_id(data: dict[str, Any] | None, key: str, domain: str, name: str) -> str:
    """Entity id for one container action or reading."""
    return ravn_entity_id(domain, container_entity_stem(data, key), name)


def container_by_key(data: dict[str, Any] | None) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Index containers by their stable key."""
    return {container["key"]: (machine, container) for machine, container in iter_containers(data)}


def machines_for_entry(
    entry: ConfigEntry,
    data: dict[str, Any] | None,
    coordinators: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the snapshot machines this config entry should show.

    Each entry is one machine. It shows the environment it was linked to, or
    the environment whose host matches its agent.
    """
    machines = list((data or {}).get("machines") or [])
    env_id = str(entry.data.get(CONF_ARCANE_ENV) or "")
    if env_id:
        return [machine for machine in machines if str(machine.get("env_id") or "") == env_id]
    nodes = node_matches(entry, coordinators)
    if not nodes:
        return []
    owned: list[dict[str, Any]] = []
    for machine in machines:
        _parent, synthetic = match_parent(machine, nodes)
        if not synthetic:
            owned.append(machine)
    return owned


def iter_entry_containers(
    entry: ConfigEntry,
    data: dict[str, Any] | None,
    coordinators: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return containers that belong on this machine's entry."""
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for machine in machines_for_entry(entry, data, coordinators):
        for container in machine.get("containers") or []:
            found.append((machine, container))
    return found


def _domain_identifier(device: dr.DeviceEntry | dr.ChildDeviceEntry) -> str | None:
    """Return this device's RAVN identifier, if it has one."""
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


def _container_device(container: dict[str, Any], url: str) -> DeviceInfo:
    """Device info for one container. The model is the row subtitle in Settings."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"arcane-container:{container['key']}")},
        name=str(container.get("name") or container["key"]),
        model="Docker container",
        configuration_url=url,
    )


def sync_arcane_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ArcaneCoordinator | None,
    coordinators: dict[str, Any],
) -> None:
    """Register each container as its own device on this machine's entry.

    The collapsible row is the config entry, so containers are not nested under
    another device. A machine with no RAVN agent has no extra device.
    """
    if coordinator is None or not coordinator.data:
        return
    devices = dr.async_get(hass)
    wanted: set[str] = set()
    rows: list[dict[str, Any]] = []
    url = coordinator.url
    for _machine, container in iter_entry_containers(entry, coordinator.data, coordinators):
        wanted.add(f"arcane-container:{container['key']}")
        rows.append(container)
    # Children inherit their parent's model and draw the dotted line. Promote
    # them to normal devices, and drop the empty host device those lines hung from.
    for device in list(dr.async_child_entries_for_config_entry(devices, entry.entry_id)):
        identifier = _domain_identifier(device)
        if identifier and identifier.startswith("arcane-container:"):
            devices.async_remove_device(device.id)
    for device in list(dr.async_entries_for_config_entry(devices, entry.entry_id)):
        identifier = _domain_identifier(device)
        if identifier is None:
            continue
        if identifier.startswith("arcane-machine:"):
            devices.async_remove_device(device.id)
        elif identifier.startswith("arcane-container:") and identifier not in wanted:
            devices.async_remove_device(device.id)
    for container in rows:
        info = _container_device(container, url)
        devices.async_get_or_create(config_entry_id=entry.entry_id, **info)


def drop_arcane_entities(hass: HomeAssistant, entry: ConfigEntry, domain: str, keep: set[str]) -> None:
    """Remove registry entries for containers that are no longer listed."""
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == domain and item.unique_id.startswith("arcane:") and item.unique_id not in keep:
            registry.async_remove(item.entity_id)


class ArcaneContainerEntity(CoordinatorEntity[ArcaneCoordinator]):
    """An entity on one container device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ArcaneCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key

    def _pair(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        return container_by_key(self.coordinator.data).get(self._key)

    @property
    def available(self) -> bool:
        pair = self._pair()
        if not super().available or pair is None:
            return False
        machine, container = pair
        if machine.get("online") is False or container.get("missing"):
            return False
        return True

    def container_note_attributes(self) -> dict[str, str]:
        """Custom URL and name, also exposed on the container's other entities."""
        notes = {"url": "", "name": ""}
        reader = getattr(self.coordinator, "container_notes", None)
        if reader is not None:
            notes = reader(self._key)
        return {
            "custom_url": str(notes.get("url") or ""),
            "custom_name": str(notes.get("name") or ""),
        }

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return self.container_note_attributes()

    @property
    def device_info(self) -> DeviceInfo:
        pair = self._pair()
        container = pair[1] if pair else {"name": self._key, "key": self._key}
        return _container_device(container, self.coordinator.url)

    async def _run(self, action: str, timeout: float) -> None:
        pair = self._pair()
        if pair is None or not pair[1].get("docker_id"):
            raise HomeAssistantError("Container is not in the latest list")
        container = pair[1]
        try:
            await self.coordinator.client.action(
                container["env_id"],
                container["docker_id"],
                action,
                timeout=timeout,
            )
        except ArcaneError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
