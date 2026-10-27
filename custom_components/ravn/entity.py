"""Shared device info for every RAVN entity."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ravn_entity_id
from .coordinator import RavnCoordinator


def node_entity_id(coordinator: RavnCoordinator, entry: ConfigEntry, domain: str, name: str) -> str:
    """Entity id for one node, using the hostname so two agents do not collide."""
    reported = str((coordinator.data or {}).get("name") or "").strip()
    if not reported:
        title = str(entry.title or "")
        reported = title.split("•", 1)[0].strip() if title else ""
    return ravn_entity_id(domain, reported or coordinator.host, name)


class RavnEntity(CoordinatorEntity[RavnCoordinator]):
    """An entity attached to one node."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RavnCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._node_id = coordinator.node_id

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data or {}
        host = self.coordinator.host
        return DeviceInfo(
            identifiers={(DOMAIN, self._node_id)},
            name="ravn-agent",
            manufacturer="RAVN",
            model="Node agent",
            sw_version=data.get("version"),
            configuration_url=f"https://{host}:{self.coordinator.port}",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
