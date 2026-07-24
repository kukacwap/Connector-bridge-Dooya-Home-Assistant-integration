"""Cover platform for Connector Bridge blinds."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import ConnectorBlind, ConnectorGateway
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

# The gateway cannot handle concurrent requests. Tell Home Assistant to
# update this platform's entities one at a time instead of in parallel;
# combined with the gateway-level lock this keeps traffic serialized.
PARALLEL_UPDATES = 1

BLIND_TYPE_TO_DEVICE_CLASS: dict[int, CoverDeviceClass] = {
    1: CoverDeviceClass.BLIND,
    2: CoverDeviceClass.BLIND,
    3: CoverDeviceClass.BLIND,
    6: CoverDeviceClass.SHUTTER,
    7: CoverDeviceClass.GATE,
    8: CoverDeviceClass.AWNING,
    12: CoverDeviceClass.CURTAIN,
    13: CoverDeviceClass.CURTAIN,
    14: CoverDeviceClass.CURTAIN,
    22: CoverDeviceClass.SHUTTER,
}

# Bridge operation codes (from data.operation in responses)
_OP_CLOSE = 0
_OP_OPEN = 1
_OP_STOP = 2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: ConnectorGateway = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ConnectorBridgeCover(gateway, blind, entry)
        for blind in gateway.device_list.values()
    ]
    async_add_entities(entities, update_before_add=True)


class ConnectorBridgeCover(CoverEntity):
    """Motorized blind via Connector Bridge."""

    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(
        self,
        gateway: ConnectorGateway,
        blind: ConnectorBlind,
        entry: ConfigEntry,
    ) -> None:
        self._gateway = gateway
        self._blind = blind
        self._entry = entry

        self._attr_unique_id = f"{DOMAIN}_{blind.mac}"
        self._attr_name = f"Blind {blind.mac[-4:]}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, blind.mac)},
            name=f"Blind {blind.mac[-4:]}",
            manufacturer="Dooya",
            model=blind.device_type,
            via_device=(DOMAIN, gateway.mac or ""),
        )

        # Start with basic features; SET_POSITION added after first successful
        # position read (bi-directional motors only).
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )
        self._has_position = False

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        self._blind.register_callback(self.entity_id, self._on_push_update)

    async def async_will_remove_from_hass(self) -> None:
        self._blind.remove_callback(self.entity_id)

    @callback
    def _on_push_update(self) -> None:
        self._update_position_feature()
        self.async_write_ha_state()

    def _update_position_feature(self) -> None:
        """Enable SET_POSITION feature once we know the blind reports position."""
        if not self._has_position and self._blind.position is not None:
            self._has_position = True
            self._attr_supported_features |= CoverEntityFeature.SET_POSITION

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._blind.available

    @property
    def current_cover_position(self) -> int | None:
        """
        HA convention: 0 = fully closed, 100 = fully open.
        Bridge convention: 0 = open, 100 = closed  →  invert here.
        Returns None only if the blind has never reported a position
        (unidirectional motor). HomeKit will treat None as 0 (closed).
        """
        return self._blind.ha_position

    @property
    def is_closed(self) -> bool | None:
        pos = self._blind.ha_position
        if pos is None:
            # Unknown state — let HA show as unavailable rather than wrong
            return None
        return pos == 0

    @property
    def is_opening(self) -> bool:
        # operation 1 = Opening; only true while actively moving open
        return self._blind._status == _OP_OPEN

    @property
    def is_closing(self) -> bool:
        # operation 0 = Closing; only true while actively moving closed
        return self._blind._status == _OP_CLOSE

    @property
    def device_class(self) -> CoverDeviceClass:
        btype = self._blind._blind_type
        return BLIND_TYPE_TO_DEVICE_CLASS.get(btype, CoverDeviceClass.BLIND)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_open_cover(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._blind.open)

    async def async_close_cover(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._blind.close)

    async def async_stop_cover(self, **kwargs) -> None:
        await self.hass.async_add_executor_job(self._blind.stop)

    async def async_set_cover_position(self, **kwargs) -> None:
        """
        HA sends position 0–100 (0=closed, 100=open).
        Bridge expects 0–100 (0=open, 100=closed) → invert.
        """
        ha_pos: int = kwargs[ATTR_POSITION]
        bridge_pos = 100 - ha_pos
        await self.hass.async_add_executor_job(self._blind.set_position, bridge_pos)

    # ------------------------------------------------------------------
    # Polling fallback (when multicast push is unavailable)
    # ------------------------------------------------------------------

    async def async_update(self) -> None:
        try:
            await self.hass.async_add_executor_job(self._blind.update)
            self._update_position_feature()
        except Exception as err:
            _LOGGER.debug("Poll failed for blind %s: %s", self._blind.mac, err)
