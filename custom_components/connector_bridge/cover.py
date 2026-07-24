"""Cover platform for Connector Bridge blinds."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import ConnectorBlind, ConnectorGateway
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TRAVEL_TIME, DOMAIN, OPT_TRAVEL_TIMES
from .travelcalculator import TravelCalculator

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

# The gateway cannot handle concurrent requests. Tell Home Assistant to
# update this platform's entities one at a time instead of in parallel;
# combined with the gateway-level lock this keeps traffic serialized.
PARALLEL_UPDATES = 1

# How often to refresh the estimated position while a blind is moving.
_TRAVEL_UPDATE_INTERVAL = timedelta(seconds=1)

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
    travel_times: dict = entry.options.get(OPT_TRAVEL_TIMES, {})
    entities = [
        ConnectorBridgeCover(
            gateway,
            blind,
            entry,
            float(travel_times.get(blind.mac, DEFAULT_TRAVEL_TIME)),
        )
        for blind in gateway.device_list.values()
    ]
    async_add_entities(entities, update_before_add=True)


class ConnectorBridgeCover(CoverEntity, RestoreEntity):
    """Motorized blind via Connector Bridge.

    Motors that report a real position use it directly. Open/close-only
    (stateless) motors have no position feedback, so we estimate one from
    the configured travel time and treat the entity as assumed-state. The
    estimate is driven by whatever last triggered the blind — a command from
    Home Assistant/HomeKit, an automation, or a gateway push report.
    """

    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(
        self,
        gateway: ConnectorGateway,
        blind: ConnectorBlind,
        entry: ConfigEntry,
        travel_time: float,
    ) -> None:
        self._gateway = gateway
        self._blind = blind
        self._entry = entry
        self._travel = TravelCalculator(travel_time)
        self._unsub_travel = None

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
        await super().async_added_to_hass()
        self._blind.register_callback(self.entity_id, self._on_push_update)

        # Restore the last assumed position across restarts.
        last_state = await self.async_get_last_state()
        if last_state is not None:
            pos = last_state.attributes.get(ATTR_CURRENT_POSITION)
            if pos is not None:
                try:
                    self._travel.set_position(float(pos))
                except (TypeError, ValueError):
                    pass

    async def async_will_remove_from_hass(self) -> None:
        self._stop_travel_updates()
        self._blind.remove_callback(self.entity_id)

    @callback
    def _on_push_update(self) -> None:
        self._update_position_feature()

        # For stateless blinds, drive the estimate from the reported
        # operation so externally triggered moves are reflected too.
        if self._blind.ha_position is None:
            status = self._blind.operation
            if status == _OP_OPEN and not self._travel.is_traveling():
                self._travel.start_travel(100)
                self._start_travel_updates()
            elif status == _OP_CLOSE and not self._travel.is_traveling():
                self._travel.start_travel(0)
                self._start_travel_updates()
            elif status == _OP_STOP:
                self._travel.stop()
                self._stop_travel_updates()

        self.async_write_ha_state()

    def _update_position_feature(self) -> None:
        """Enable SET_POSITION feature once we know the blind reports position."""
        if not self._has_position and self._blind.position is not None:
            self._has_position = True
            self._attr_supported_features |= CoverEntityFeature.SET_POSITION

    # ------------------------------------------------------------------
    # Travel animation
    # ------------------------------------------------------------------

    @callback
    def _start_travel_updates(self) -> None:
        if self._unsub_travel is None:
            self._unsub_travel = async_track_time_interval(
                self.hass, self._async_travel_tick, _TRAVEL_UPDATE_INTERVAL
            )

    @callback
    def _stop_travel_updates(self) -> None:
        if self._unsub_travel is not None:
            self._unsub_travel()
            self._unsub_travel = None

    @callback
    def _async_travel_tick(self, now) -> None:
        if self._travel.finalize_if_arrived() or not self._travel.is_traveling():
            self._stop_travel_updates()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def assumed_state(self) -> bool:
        """True for stateless blinds whose position we can only estimate."""
        return self._blind.ha_position is None

    @property
    def available(self) -> bool:
        return self._blind.available

    @property
    def current_cover_position(self) -> int | None:
        """HA convention: 0 = closed, 100 = open.

        Uses the real position when the motor reports one, otherwise the
        time-based estimate.
        """
        real = self._blind.ha_position
        if real is not None:
            return real
        return int(round(self._travel.current_position()))

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    @property
    def is_opening(self) -> bool:
        if self._blind.ha_position is not None:
            return self._blind.operation == _OP_OPEN
        return self._travel.travel_direction() == 1

    @property
    def is_closing(self) -> bool:
        if self._blind.ha_position is not None:
            return self._blind.operation == _OP_CLOSE
        return self._travel.travel_direction() == -1

    @property
    def device_class(self) -> CoverDeviceClass:
        return BLIND_TYPE_TO_DEVICE_CLASS.get(
            self._blind.blind_type, CoverDeviceClass.BLIND
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_open_cover(self, **kwargs) -> None:
        if self.assumed_state:
            self._travel.start_travel(100)
            self._start_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.open)

    async def async_close_cover(self, **kwargs) -> None:
        if self.assumed_state:
            self._travel.start_travel(0)
            self._start_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.close)

    async def async_stop_cover(self, **kwargs) -> None:
        if self.assumed_state:
            self._travel.stop()
            self._stop_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.stop)

    async def async_set_cover_position(self, **kwargs) -> None:
        """
        HA sends position 0-100 (0=closed, 100=open).
        Bridge expects 0-100 (0=open, 100=closed) -> invert.
        """
        ha_pos: int = kwargs[ATTR_POSITION]
        if self.assumed_state:
            self._travel.start_travel(ha_pos)
            self._start_travel_updates()
            self.async_write_ha_state()
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
