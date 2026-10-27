"""Restart and redeploy buttons for Arcane containers."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ARCANE, ravn_entity_id
from .containers import (
    CONTAINER_ACTIONS,
    ArcaneContainerEntity,
    container_entity_id,
    drop_arcane_entities,
    iter_entry_containers,
)
from .coordinator import ArcaneCoordinator

PARALLEL_UPDATES = 1

CLEANUP_UNIQUE_ID = "arcane:cleanup_missing"
_ACTION_TIMEOUT = {"restart": 60, "redeploy": 120}
_ACTION_ICON = {"redeploy": "mdi:reload"}
_ACTION_NAME = {"restart": "Restart", "redeploy": "Redeploy"}


def _setup_containers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ArcaneCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create restart and redeploy for each container."""
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[ArcaneActionButton] = []
        keep: set[str] = {CLEANUP_UNIQUE_ID}
        runtime = entry.runtime_data
        for _machine, container in iter_entry_containers(entry, coordinator.data, runtime.coordinators):
            for action in CONTAINER_ACTIONS:
                unique_id = f"arcane:{container['key']}:{action}"
                keep.add(unique_id)
                token = f"{container['key']}:{action}"
                if token in added:
                    continue
                added.add(token)
                fresh.append(ArcaneActionButton(coordinator, entry, container["key"], action))
        if fresh:
            async_add_entities(fresh)
        for token in list(added):
            key, action = token.rsplit(":", 1)
            if f"arcane:{key}:{action}" not in keep:
                added.discard(token)
        drop_arcane_entities(hass, entry, "button", keep)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create container actions when an Arcane manager is configured."""
    arcane = entry.runtime_data.arcane
    if arcane is None:
        return
    if isinstance(entry.data.get(CONF_ARCANE), dict):
        async_add_entities([ArcaneCleanupButton(arcane)])
    _setup_containers(hass, entry, arcane, async_add_entities)


class ArcaneActionButton(ArcaneContainerEntity, ButtonEntity):
    """One container action."""

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ConfigEntry,
        key: str,
        action: str,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._action = action
        self._attr_unique_id = f"arcane:{key}:{action}"
        self._attr_name = _ACTION_NAME[action]
        if action == "restart":
            self._attr_device_class = ButtonDeviceClass.RESTART
        else:
            self._attr_icon = _ACTION_ICON[action]
        self.entity_id = container_entity_id(coordinator.data, key, "button", self._attr_name)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        pair = self._pair()
        if pair is None:
            return False
        if self._action == "redeploy":
            return not pair[1]["redeploy_disabled"]
        return True

    async def async_press(self) -> None:
        """Ask the manager to run this action, then read the list again."""
        await self._run(self._action, _ACTION_TIMEOUT[self._action])


class ArcaneCleanupButton(ButtonEntity):
    """Remove containers that are already known to be gone, without waiting."""

    _attr_has_entity_name = False
    _attr_name = "Clean up missing"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:broom"
    _attr_unique_id = CLEANUP_UNIQUE_ID

    def __init__(self, coordinator: ArcaneCoordinator) -> None:
        self.coordinator = coordinator
        self.entity_id = ravn_entity_id("button", self._attr_name)

    async def async_press(self) -> None:
        """Drop vanished containers now. An offline machine is left as it is."""
        await self.coordinator.async_cleanup_now()
