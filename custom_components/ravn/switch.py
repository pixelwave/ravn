"""Switches that enable or disable a sensor on the node."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import RavnError
from .containers import (
    ArcaneContainerEntity,
    container_entity_id,
    drop_arcane_entities,
    iter_entry_containers,
)
from .coordinator import ArcaneCoordinator, RavnCoordinator
from .entity import RavnEntity, node_entity_id

PARALLEL_UPDATES = 1


def controllable(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return sensors the user can turn on and off."""
    return [sensor for sensor in data.get("catalog") or [] if sensor.get("controllable")]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create node switches, and one running switch per container."""
    for coordinator in entry.runtime_data.coordinators.values():
        _setup_node(entry, coordinator, async_add_entities)
    arcane = entry.runtime_data.arcane
    if arcane is not None:
        _setup_containers(hass, entry, arcane, async_add_entities)


def _setup_node(
    entry: ConfigEntry,
    coordinator: RavnCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the enable switches for one node."""
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[RavnEnableSwitch] = []
        for sensor in controllable(coordinator.data or {}):
            sensor_id = sensor["id"]
            if sensor_id in added:
                continue
            added.add(sensor_id)
            fresh.append(RavnEnableSwitch(coordinator, entry, sensor_id, sensor.get("name") or sensor_id))
        if fresh:
            async_add_entities(fresh)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class RavnEnableSwitch(RavnEntity, SwitchEntity):
    """Writes the sensor's enabled flag to the agent."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:eye-settings"

    def __init__(
        self,
        coordinator: RavnCoordinator,
        entry: ConfigEntry,
        sensor_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._sensor_id = sensor_id
        self._attr_unique_id = f"{self._node_id}_{sensor_id}_enabled"
        self._attr_name = name
        self.entity_id = node_entity_id(coordinator, entry, "switch", name)

    @property
    def is_on(self) -> bool:
        for sensor in controllable(self.coordinator.data or {}):
            if sensor.get("id") == self._sensor_id:
                return bool(sensor.get("enabled"))
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, enabled: bool) -> None:
        interval = 30
        for sensor in controllable(self.coordinator.data or {}):
            if sensor.get("id") == self._sensor_id:
                interval = int(sensor.get("interval_s") or 30)
                break
        try:
            await self.coordinator.client.put_sensor(self._sensor_id, enabled, interval)
        except RavnError as err:
            raise HomeAssistantError(str(err)) from err
        # async_request_refresh is debounced for 10s, so a toggle would look unchanged.
        await self.coordinator.async_refresh()


def _setup_containers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ArcaneCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the running switch for each container."""
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[ArcaneRunningSwitch | ArcaneExcludeSwitch] = []
        keep: set[str] = set()
        runtime = entry.runtime_data
        for _machine, container in iter_entry_containers(entry, coordinator.data, runtime.coordinators):
            key = container["key"]
            for unique_id, factory in (
                (f"arcane:{key}:running", ArcaneRunningSwitch),
                (f"arcane:{key}:exclude", ArcaneExcludeSwitch),
            ):
                keep.add(unique_id)
                if unique_id in added:
                    continue
                added.add(unique_id)
                fresh.append(factory(coordinator, entry, key))
        if fresh:
            async_add_entities(fresh)
        for unique_id in list(added):
            if unique_id not in keep:
                added.discard(unique_id)
        drop_arcane_entities(hass, entry, "switch", keep)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class ArcaneRunningSwitch(ArcaneContainerEntity, SwitchEntity):
    """Start or stop one container. On means Docker reports it running."""

    _attr_icon = "mdi:power"
    _attr_name = "Running"

    def __init__(self, coordinator: ArcaneCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_unique_id = f"arcane:{key}:running"
        self.entity_id = container_entity_id(coordinator.data, key, "switch", self._attr_name)

    @property
    def is_on(self) -> bool | None:
        pair = self._pair()
        if pair is None:
            return None
        return pair[1].get("state") == "running"

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        pair = self._pair()
        if pair is None:
            return None
        return {
            "status": str(pair[1].get("status") or ""),
            "image": str(pair[1].get("image") or ""),
            **self.container_note_attributes(),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run("start", 30)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run("stop", 30)


class ArcaneExcludeSwitch(ArcaneContainerEntity, SwitchEntity):
    """Keep this container out of dashboards that honor the flag. Off by default."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:eye-off"
    _attr_name = "Exclude container"

    def __init__(self, coordinator: ArcaneCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_unique_id = f"arcane:{key}:exclude"
        self.entity_id = container_entity_id(coordinator.data, key, "switch", self._attr_name)

    @property
    def available(self) -> bool:
        """Stay editable while the container is still listed, including while it is offline."""
        return self._pair() is not None

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.container_notes(self._key).get("exclude"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_container_note(self._key, "exclude", "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_container_note(self._key, "exclude", "off")
