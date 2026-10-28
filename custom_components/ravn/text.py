"""Custom URL and name for one Docker container."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .containers import (
    ArcaneContainerEntity,
    container_entity_id,
    drop_arcane_entities,
    iter_entry_containers,
)
from .coordinator import ArcaneCoordinator

PARALLEL_UPDATES = 1

# Home Assistant stores an entity state in at most 255 characters.
_MAX_LENGTH = 255

_FIELDS = (
    ("url", "Custom URL", "mdi:link-variant"),
    ("name", "Custom name", "mdi:rename-box"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the two configuration fields for each container."""
    arcane = entry.runtime_data.arcane
    if arcane is None:
        return
    added: set[str] = set()

    def _sync() -> None:
        fresh: list[ArcaneContainerText] = []
        keep: set[str] = set()
        runtime = entry.runtime_data
        for _machine, container in iter_entry_containers(entry, arcane.data, runtime.coordinators):
            for field, _label, _icon in _FIELDS:
                unique_id = f"arcane:{container['key']}:{field}"
                keep.add(unique_id)
                if unique_id in added:
                    continue
                added.add(unique_id)
                fresh.append(ArcaneContainerText(arcane, entry, container["key"], field))
        if fresh:
            async_add_entities(fresh)
        for unique_id in list(added):
            if unique_id not in keep:
                added.discard(unique_id)
        drop_arcane_entities(hass, entry, "text", keep)

    _sync()
    entry.async_on_unload(arcane.async_add_listener(_sync))


class ArcaneContainerText(ArcaneContainerEntity, TextEntity):
    """One user-entered string stored for a container."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = _MAX_LENGTH

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ConfigEntry,
        key: str,
        field: str,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._field = field
        label, icon = next((item[1], item[2]) for item in _FIELDS if item[0] == field)
        self._attr_name = label
        self._attr_icon = icon
        self._attr_unique_id = f"arcane:{key}:{field}"
        self.entity_id = container_entity_id(coordinator.data, key, "text", label)

    @property
    def available(self) -> bool:
        """Stay editable while the container is still listed, including while it is offline."""
        return self._pair() is not None

    @property
    def native_value(self) -> str:
        return self.container_note_attributes()["custom_url" if self._field == "url" else "custom_name"]

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_container_note(self._key, self._field, value[:_MAX_LENGTH])
