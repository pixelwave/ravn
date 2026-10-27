"""Interval controls, in seconds, stored on the node."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import RavnError
from .entity import RavnEntity, node_entity_id
from .coordinator import RavnCoordinator
from .switch import controllable

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one interval number per controllable sensor on every node."""
    for coordinator in entry.runtime_data.coordinators.values():
        _setup_node(entry, coordinator, async_add_entities)


def _setup_node(
    entry: ConfigEntry,
    coordinator: RavnCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the interval controls for one node."""
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[RavnIntervalNumber] = []
        for sensor in controllable(coordinator.data or {}):
            sensor_id = sensor["id"]
            if sensor_id in added:
                continue
            added.add(sensor_id)
            fresh.append(
                RavnIntervalNumber(coordinator, entry, sensor_id, sensor.get("name") or sensor_id)
            )
        if fresh:
            async_add_entities(fresh)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class RavnIntervalNumber(RavnEntity, NumberEntity):
    """Check interval for one sensor."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 86400
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer-cog"

    def __init__(
        self,
        coordinator: RavnCoordinator,
        entry: ConfigEntry,
        sensor_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._sensor_id = sensor_id
        self._attr_unique_id = f"{self._node_id}_{sensor_id}_interval"
        self._attr_name = f"{name} interval"
        self.entity_id = node_entity_id(coordinator, entry, "number", self._attr_name)

    @property
    def native_value(self) -> float:
        for sensor in controllable(self.coordinator.data or {}):
            if sensor.get("id") == self._sensor_id:
                return float(sensor.get("interval_s") or 30)
        return 30

    async def async_set_native_value(self, value: float) -> None:
        enabled = False
        for sensor in controllable(self.coordinator.data or {}):
            if sensor.get("id") == self._sensor_id:
                enabled = bool(sensor.get("enabled"))
                break
        try:
            await self.coordinator.client.put_sensor(self._sensor_id, enabled, int(value))
        except RavnError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_refresh()
