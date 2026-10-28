"""Stored nodes for the single RAVN config entry."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ARCANE,
    CONF_ARCANE_ENV,
    CONF_ARCANE_MANAGER,
    CONF_AUTO_TITLE,
    CONF_FINGERPRINT,
    CONF_MACHINE_IP,
    CONF_MACHINE_NAME,
    CONF_NODE_ID,
    CONF_NODES,
    CONF_SHOW_UPDATES,
    CONF_STALE_HOURS,
    DEFAULT_PORT,
    DEFAULT_STALE_HOURS,
    DOMAIN,
)

HUB_UNIQUE_ID = "local-network"
HUB_TITLE = "Local Network"


def node_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return one node record."""
    return {
        CONF_NODE_ID: str(data[CONF_NODE_ID]),
        "host": data["host"],
        "port": int(data.get("port") or DEFAULT_PORT),
        "token": data["token"],
        CONF_FINGERPRINT: data[CONF_FINGERPRINT],
    }


def entry_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Read nodes from the current or the older one-node entry shape."""
    if CONF_NODES in data:
        return [node_dict(node) for node in data[CONF_NODES]]
    if data.get(CONF_NODE_ID):
        return [node_dict(data)]
    return []


def managed_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return RAVN entries the hub should own."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.source != SOURCE_IGNORE and entry.disabled_by is None
    ]


def hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the RAVN entry that should own every node."""
    entries = managed_entries(hass)
    for entry in entries:
        if entry.unique_id == HUB_UNIQUE_ID:
            return entry
    return entries[0] if entries else None


def find_node(entry: ConfigEntry, node_id: str) -> dict[str, Any] | None:
    """Return the stored node, if this entry has it."""
    for node in entry_nodes(entry.data):
        if node[CONF_NODE_ID] == node_id:
            return node
    return None


def entry_payload(data: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return entry data with these nodes, keeping Arcane fields that are set."""
    payload: dict[str, Any] = {CONF_NODES: [node_dict(node) for node in nodes]}
    arcane = data.get(CONF_ARCANE)
    if isinstance(arcane, dict) and arcane.get("url") and arcane.get("api_key"):
        payload[CONF_ARCANE] = {
            "url": str(arcane["url"]),
            "api_key": str(arcane["api_key"]),
            "scan_interval": int(arcane.get("scan_interval") or 60),
            "include_remote": bool(arcane.get("include_remote", True)),
            CONF_SHOW_UPDATES: bool(arcane.get(CONF_SHOW_UPDATES, False)),
            CONF_STALE_HOURS: (
                int(arcane[CONF_STALE_HOURS])
                if CONF_STALE_HOURS in arcane
                else DEFAULT_STALE_HOURS
            ),
        }
    for key in (CONF_ARCANE_ENV, CONF_ARCANE_MANAGER, CONF_MACHINE_NAME, CONF_MACHINE_IP, CONF_AUTO_TITLE):
        value = data.get(key)
        if value not in (None, ""):
            payload[key] = value
    return payload


def with_node(entry: ConfigEntry, node: dict[str, Any]) -> dict[str, Any]:
    """Return entry data with this node added or replaced."""
    nodes = [item for item in entry_nodes(entry.data) if item[CONF_NODE_ID] != node[CONF_NODE_ID]]
    nodes.append(node_dict(node))
    return entry_payload(dict(entry.data), nodes)


def with_arcane(entry: ConfigEntry, arcane: dict[str, Any] | None) -> dict[str, Any]:
    """Return entry data with the Arcane manager replaced or removed."""
    data = dict(entry.data)
    if arcane is None:
        data.pop(CONF_ARCANE, None)
    else:
        data[CONF_ARCANE] = arcane
    return entry_payload(data, entry_nodes(data))


def manager_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the entry that stores the Arcane URL and API key."""
    for entry in managed_entries(hass):
        arcane = entry.data.get(CONF_ARCANE)
        if isinstance(arcane, dict) and arcane.get("url") and arcane.get("api_key"):
            return entry
    return None


def entry_for_node(hass: HomeAssistant, node_id: str) -> ConfigEntry | None:
    """Return the entry that stores this agent."""
    for entry in managed_entries(hass):
        if find_node(entry, node_id) is not None:
            return entry
    return None


def machine_keys(entry: ConfigEntry) -> set[str]:
    """Names and addresses that identify the machine this entry represents."""
    keys = {str(entry.data.get(CONF_MACHINE_NAME) or "").strip().lower(), str(entry.data.get(CONF_MACHINE_IP) or "").strip().lower()}
    for node in entry_nodes(entry.data):
        keys.add(str(node.get("host") or "").strip().lower())
    keys.discard("")
    return keys


def find_entry_for_machine(hass: HomeAssistant, machine: dict[str, Any]) -> ConfigEntry | None:
    """Return the entry for this Arcane environment, if one already exists."""
    env_id = str(machine.get("env_id") or "")
    wanted = {str(machine.get("name") or "").strip().lower(), str(machine.get("ip") or "").strip().lower()}
    wanted.discard("")
    for entry in managed_entries(hass):
        if env_id and str(entry.data.get(CONF_ARCANE_ENV) or "") == env_id:
            return entry
        if env_id and entry.unique_id == f"arcane:{env_id}":
            return entry
        if machine_keys(entry) & wanted:
            return entry
    return None


def adopt_entry(hass: HomeAssistant, hub: ConfigEntry, old: ConfigEntry) -> None:
    """Move devices and entities onto the hub before the old entry is removed.

    Home Assistant 2026 keeps a device on one config entry. ``new_config_entry_id``
    moves it. Adding and removing config entry ids no longer does.
    """
    if old.entry_id == hub.entry_id:
        return
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, old.entry_id):
        devices.async_update_device(device.id, new_config_entry_id=hub.entry_id)
    for entity in er.async_entries_for_config_entry(entities, old.entry_id):
        entities.async_update_entity(entity.entity_id, config_entry_id=hub.entry_id)
