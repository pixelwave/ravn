"""Sensors created from the agent catalog."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .containers import (
    ArcaneContainerEntity,
    container_entity_id,
    drop_arcane_entities,
    iter_entry_containers,
)
from .entity import RavnEntity, node_entity_id
from .coordinator import ArcaneCoordinator, RavnCoordinator

PARALLEL_UPDATES = 0


def _device_class(value: str | None) -> SensorDeviceClass | None:
    if not value:
        return None
    try:
        return SensorDeviceClass(value)
    except ValueError:
        return None


def _state_class(value: str | None) -> SensorStateClass | None:
    if not value:
        return None
    try:
        return SensorStateClass(value)
    except ValueError:
        return None


def reading_metas(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return catalog entries that should exist as reading sensors."""
    found: list[dict[str, Any]] = []
    for sensor in data.get("catalog") or []:
        if sensor.get("id") in {"name", "ip"} or not sensor.get("enabled"):
            continue
        if sensor.get("id") == "fans":
            found.extend(sensor.get("members") or [])
            continue
        if sensor.get("controllable"):
            found.append(sensor)
    return found


def _drop_disabled(hass: HomeAssistant, entry: ConfigEntry, node_id: str, want: dict[str, Any]) -> None:
    """Remove reading entities for sensors that are off.

    Home Assistant keeps a registry entry after a plain entity removal and
    shows it as unavailable. Dropping the registry entry removes the state too.
    """
    registry = er.async_get(hass)
    keep = {f"{node_id}_name", f"{node_id}_ip"}
    keep.update(f"{node_id}_{sensor_id}" for sensor_id in want)
    prefix = f"{node_id}_"
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == "sensor" and item.unique_id.startswith(prefix) and item.unique_id not in keep:
            registry.async_remove(item.entity_id)


def _setup_node(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: RavnCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create name, IP, and one sensor per enabled reading for one node."""
    async_add_entities([RavnNameSensor(coordinator, entry), RavnIPSensor(coordinator, entry)])
    added: dict[str, RavnReadingSensor] = {}

    def _sync() -> None:
        want = {meta["id"]: meta for meta in reading_metas(coordinator.data or {})}
        fresh: list[RavnReadingSensor] = []
        for sensor_id, meta in want.items():
            if sensor_id in added:
                continue
            entity = RavnReadingSensor(coordinator, entry, meta)
            added[sensor_id] = entity
            fresh.append(entity)
        if fresh:
            async_add_entities(fresh)
        for sensor_id in list(added):
            if sensor_id not in want:
                added.pop(sensor_id)
        _drop_disabled(hass, entry, coordinator.node_id, want)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


def _setup_containers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ArcaneCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one status sensor per container."""
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[ArcaneStatusSensor] = []
        keep: set[str] = set()
        runtime = entry.runtime_data
        for _machine, container in iter_entry_containers(entry, coordinator.data, runtime.coordinators):
            unique_id = f"arcane:{container['key']}:status"
            keep.add(unique_id)
            if container["key"] in added:
                continue
            added.add(container["key"])
            fresh.append(ArcaneStatusSensor(coordinator, entry, container["key"]))
        if fresh:
            async_add_entities(fresh)
        for key in list(added):
            if f"arcane:{key}:status" not in keep:
                added.discard(key)
        drop_arcane_entities(hass, entry, "sensor", keep)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for every node and for Arcane containers."""
    for coordinator in entry.runtime_data.coordinators.values():
        _setup_node(hass, entry, coordinator, async_add_entities)
    arcane = entry.runtime_data.arcane
    if arcane is not None:
        _setup_containers(hass, entry, arcane, async_add_entities)


class RavnNameSensor(RavnEntity, SensorEntity):
    """Machine name. The agent refreshes it only when the hostname changes."""

    _attr_translation_key = "machine_name"
    _attr_name = "Machine name"
    _attr_icon = "mdi:desktop-classic"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RavnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._node_id}_name"
        self.entity_id = node_entity_id(coordinator, entry, "sensor", self._attr_name)

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("name")


class RavnIPSensor(RavnEntity, SensorEntity):
    """Primary address, with the full address lists as attributes."""

    _attr_translation_key = "ip_address"
    _attr_name = "IP address"
    _attr_icon = "mdi:ip-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RavnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._node_id}_ip"
        self.entity_id = node_entity_id(coordinator, entry, "sensor", self._attr_name)

    @property
    def native_value(self) -> str | None:
        ip = (self.coordinator.data or {}).get("ip") or {}
        return ip.get("primary") or None

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        ip = (self.coordinator.data or {}).get("ip") or {}
        return {"ipv4": list(ip.get("ipv4") or []), "ipv6": list(ip.get("ipv6") or [])}


class RavnReadingSensor(RavnEntity, SensorEntity):
    """One cached metric."""

    def __init__(
        self,
        coordinator: RavnCoordinator,
        entry: ConfigEntry,
        meta: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry)
        self._sensor_id = meta["id"]
        self._attr_unique_id = f"{self._node_id}_{self._sensor_id}"
        self._attr_name = meta.get("name")
        self.entity_id = node_entity_id(coordinator, entry, "sensor", str(self._attr_name or self._sensor_id))
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_device_class = _device_class(meta.get("device_class"))
        self._attr_state_class = _state_class(meta.get("state_class"))
        self._attr_icon = meta.get("icon")
        # Precision marks a numeric sensor. Load is text, so it must not get one.
        if meta.get("unit") or meta.get("state_class") or meta.get("device_class"):
            precision = meta.get("precision")
            if precision is not None:
                self._attr_suggested_display_precision = int(precision)

    @property
    def available(self) -> bool:
        return super().available and bool(self._sample().get("available"))

    @property
    def native_value(self) -> float | str | None:
        if not self._sample().get("available"):
            return None
        text = self._sample().get("text")
        if text:
            return str(text)
        value = self._sample().get("value")
        return None if value is None else float(value)

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        attrs = self._sample().get("attributes")
        if isinstance(attrs, dict) and attrs:
            return {str(key): float(value) for key, value in attrs.items()}
        return None

    def _sample(self) -> dict[str, Any]:
        readings = (self.coordinator.data or {}).get("readings") or {}
        sample = readings.get(self._sensor_id) or {}
        return sample if isinstance(sample, dict) else {}


class ArcaneStatusSensor(ArcaneContainerEntity, SensorEntity):
    """Docker state for one container."""

    _attr_name = "Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: ArcaneCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_unique_id = f"arcane:{key}:status"
        self.entity_id = container_entity_id(coordinator.data, key, "sensor", self._attr_name)

    @property
    def native_value(self) -> str | None:
        pair = self._pair()
        if pair is None:
            return None
        return pair[1]["state"]

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        pair = self._pair()
        if pair is None:
            return None
        container = pair[1]
        return {
            "status": container["status"],
            "image": container["image"],
            "environment": pair[0]["title"],
        }
