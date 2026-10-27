"""Update entities for the agent binary and for Arcane container images."""

from __future__ import annotations

import asyncio

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .arcane import ArcaneError
from .client import RavnError
from .containers import (
    ArcaneContainerEntity,
    container_entity_id,
    drop_arcane_entities,
    iter_entry_containers,
)
from .const import CONF_ARCANE, CONF_SHOW_UPDATES
from .coordinator import ArcaneCoordinator
from .entity import RavnEntity, node_entity_id

PARALLEL_UPDATES = 1


def _container_updates_enabled(coordinator: ArcaneCoordinator) -> bool:
    """Container image updates stay off the Home Assistant updates page unless asked."""
    raw = coordinator.config_entry.data.get(CONF_ARCANE)
    return isinstance(raw, dict) and bool(raw.get(CONF_SHOW_UPDATES, False))


def _setup_containers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ArcaneCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one image update entity per container."""
    added: set[str] = set()

    def _sync() -> None:
        if not _container_updates_enabled(coordinator):
            added.clear()
            drop_arcane_entities(hass, entry, "update", set())
            return
        fresh: list[ArcaneImageUpdate] = []
        keep: set[str] = set()
        runtime = entry.runtime_data
        for _machine, container in iter_entry_containers(entry, coordinator.data, runtime.coordinators):
            unique_id = f"arcane:{container['key']}:image"
            keep.add(unique_id)
            if container["key"] in added:
                continue
            added.add(container["key"])
            fresh.append(ArcaneImageUpdate(coordinator, entry, container["key"]))
        if fresh:
            async_add_entities(fresh)
        for key in list(added):
            if f"arcane:{key}:image" not in keep:
                added.discard(key)
        drop_arcane_entities(hass, entry, "update", keep)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create agent and container update entities."""
    async_add_entities(
        [RavnAgentUpdate(coordinator, entry) for coordinator in entry.runtime_data.coordinators.values()]
    )
    arcane = entry.runtime_data.arcane
    if arcane is not None:
        _setup_containers(hass, entry, arcane, async_add_entities)


class RavnAgentUpdate(RavnEntity, UpdateEntity):
    """Installs a newer agent build. The integration itself updates through HACS."""

    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bird"
    _attr_title = "RAVN agent"
    _attr_name = "Agent"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._node_id}_agent_update"
        self.entity_id = node_entity_id(coordinator, entry, "update", self._attr_name)

    @property
    def installed_version(self) -> str | None:
        return (self.coordinator.data or {}).get("version")

    @property
    def latest_version(self) -> str | None:
        return (self.coordinator.data or {}).get("latest_version")

    async def async_release_notes(self) -> str | None:
        try:
            info = await self.coordinator.client.update_check()
        except RavnError as err:
            raise HomeAssistantError(str(err)) from err
        if info.get("latest"):
            self.coordinator.data["latest_version"] = info["latest"]
            self.coordinator.data["release_notes"] = info.get("notes")
            self.coordinator.async_update_listeners()
        return info.get("notes") or "No release notes."

    async def async_install(self, version: str | None, backup: bool) -> None:
        try:
            await self.coordinator.client.install_update()
        except RavnError as err:
            raise HomeAssistantError(str(err)) from err
        for _ in range(20):
            try:
                await self.coordinator.async_refresh()
                return
            except UpdateFailed:
                await asyncio.sleep(1)
        raise HomeAssistantError("the agent did not come back after the update")


class ArcaneImageUpdate(ArcaneContainerEntity, UpdateEntity):
    """Image version for one container. Install pulls the newer image through Arcane."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:docker"
    _attr_name = "Image"

    @property
    def entity_picture(self) -> None:
        """Show the Docker icon. Update entities otherwise use the integration logo."""
        return None

    def __init__(self, coordinator: ArcaneCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_unique_id = f"arcane:{key}:image"
        self.entity_id = container_entity_id(coordinator.data, key, "update", self._attr_name)
        self._installing = False

    def _update(self) -> dict:
        pair = self._pair()
        if pair is None:
            return {}
        info = pair[1].get("update") or {}
        return info if isinstance(info, dict) else {}

    @property
    def title(self) -> str | None:
        pair = self._pair()
        if pair is None:
            return None
        return str(pair[1].get("image") or pair[1].get("name") or "")

    @property
    def installed_version(self) -> str | None:
        return self._update().get("installed")

    @property
    def latest_version(self) -> str | None:
        return self._update().get("latest")

    @property
    def supported_features(self) -> UpdateEntityFeature:
        if self._update().get("blocked"):
            return UpdateEntityFeature(0)
        return UpdateEntityFeature.INSTALL

    @property
    def in_progress(self) -> bool:
        return self._installing

    @property
    def release_summary(self) -> str | None:
        info = self._update()
        if info.get("blocked") and info.get("has_update"):
            return "Arcane does not allow an update of this container"
        if info.get("error"):
            return str(info["error"])
        if info.get("has_update") and info.get("update_type") == "digest":
            return "Newer image with the same tag"
        return None

    async def async_install(self, version: str | None, backup: bool) -> None:
        pair = self._pair()
        if pair is None or not pair[1].get("docker_id"):
            raise HomeAssistantError("Container is not in the latest list")
        if self._update().get("blocked"):
            raise HomeAssistantError("Arcane does not allow an update of this container")
        container = pair[1]
        self._installing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.client.update_container(container["env_id"], container["docker_id"])
        except ArcaneError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self._installing = False
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
