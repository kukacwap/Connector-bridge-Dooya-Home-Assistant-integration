"""Cover platform for Connector Bridge blinds."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import ConnectorBlind, ConnectorGateway
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRAVEL_TIME,
    DEVICE_TYPE_DR,
    DEVICE_TYPE_SUNBLIND,
    DEVICE_TYPE_WIFI_CURTAIN,
    DOMAIN,
    EVENT_BLIND_MOVED,
    OPT_TRAVEL_TIMES,
    SERVICE_MOVE_FOR_DURATION,
    model_name,
)
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

# Fallback when the motor never reports a blind type (stateless motors).
# The device class drives the HomeKit accessory category, which is what
# makes Siri phrasing ("open the curtains") match the actual hardware.
DEVICE_TYPE_TO_DEVICE_CLASS: dict[str, CoverDeviceClass] = {
    DEVICE_TYPE_WIFI_CURTAIN: CoverDeviceClass.CURTAIN,
    DEVICE_TYPE_SUNBLIND: CoverDeviceClass.AWNING,
    DEVICE_TYPE_DR: CoverDeviceClass.SHADE,
}

# Bridge operation codes (from data.operation in responses)
_OP_CLOSE = 0
_OP_OPEN = 1
_OP_STOP = 2


def _gateway_link(
    hass: HomeAssistant, entry: ConfigEntry, gateway: ConnectorGateway
) -> dict[str, Any]:
    """Return the device info keys that hang a blind off the bridge device.

    Home Assistant 2026.8 added ``via_device_id`` (the bridge's device
    registry id) and 2026.9 deprecated the old ``via_device`` identifier
    tuple. When an entity is added after an update, as these are, the
    deprecated key makes the device registry raise, so no blind could be
    added at all. The bridge device is registered during setup, before the
    platforms load, so its id is already available here.
    """
    if not gateway.mac:
        return {}
    lookup = getattr(dr, "async_get_device_id_by_identifier", None)
    if lookup is None:
        # Cores before 2026.8 only understand the identifier form.
        return {"via_device": (DOMAIN, gateway.mac)}
    try:
        device_id = lookup(hass, (DOMAIN, gateway.mac), config_entry_id=entry.entry_id)
    except ValueError:
        # Without a registered bridge device the blind is simply not linked;
        # a dangling reference would make the device registry reject it.
        return {}
    return {"via_device_id": device_id}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: ConnectorGateway = hass.data[DOMAIN][entry.entry_id]
    travel_times: dict = entry.options.get(OPT_TRAVEL_TIMES, {})
    gateway_link = _gateway_link(hass, entry, gateway)
    entities = [
        ConnectorBridgeCover(
            gateway,
            blind,
            entry,
            float(travel_times.get(blind.mac, DEFAULT_TRAVEL_TIME)),
            gateway_link,
        )
        for blind in gateway.device_list.values()
    ]
    async_add_entities(entities, update_before_add=True)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_MOVE_FOR_DURATION,
        {
            vol.Required("direction"): vol.In(["open", "close"]),
            vol.Required("duration"): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=300)
            ),
        },
        "async_move_for_duration",
    )


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
        gateway_link: dict[str, Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._blind = blind
        self._entry = entry
        # Clamped to match TravelCalculator, and so a stored travel time of
        # zero cannot divide by zero when converting a distance to seconds.
        self._travel_time = max(float(travel_time), 0.1)
        self._travel = TravelCalculator(self._travel_time)
        self._unsub_travel = None
        self._unsub_stop = None

        self._attr_unique_id = f"{DOMAIN}_{blind.mac}"
        # The cover is the primary entity of its device, so it inherits the
        # device name rather than repeating it (has_entity_name convention).
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, blind.mac)},
            name=f"Blind {blind.mac[-4:]}",
            manufacturer="Dooya",
            model=model_name(blind.device_type),
            **(gateway_link or {}),
        )

        # Position control is always offered: motors that report a position
        # use it natively, and stateless motors are positioned by running
        # them for a calculated time. HomeKit needs this to show a position
        # slider and to expose Hold Position.
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._blind.register_callback(self.entity_id, self._on_push_update)
        self._blind.register_event_callback(self.entity_id, self._on_blind_event)

        # Restore the last assumed position across restarts.
        last_state = await self.async_get_last_state()
        if last_state is not None:
            pos = last_state.attributes.get(ATTR_CURRENT_POSITION)
            if pos is not None:
                try:
                    self._travel.set_position(float(pos))
                    return
                except (TypeError, ValueError):
                    pass

        # Nothing to restore (first start, or the blind was unavailable when
        # Home Assistant stopped). The bridge remembers the last command it
        # sent, which beats assuming every blind is closed.
        if self._blind.operation == _OP_OPEN:
            self._travel.set_position(100)
        elif self._blind.operation == _OP_CLOSE:
            self._travel.set_position(0)

    async def async_will_remove_from_hass(self) -> None:
        self._stop_travel_updates()
        self._cancel_pending_stop()
        self._blind.remove_callback(self.entity_id)
        self._blind.remove_event_callback(self.entity_id)

    def _on_blind_event(self, payload: dict) -> None:
        """Called from the gateway's listener thread when the blind moves."""
        self.hass.loop.call_soon_threadsafe(self._fire_moved_event, payload)

    @callback
    def _fire_moved_event(self, payload: dict) -> None:
        """Fire a bus event so automations can react to any movement.

        ``source`` is ``external`` for moves Home Assistant did not command,
        which is how a physical remote can be used as a trigger.
        """
        self.hass.bus.async_fire(
            EVENT_BLIND_MOVED,
            {
                "entity_id": self.entity_id,
                "mac": self._blind.mac,
                "device_type": self._blind.device_type,
                **payload,
            },
        )

    def _on_push_update(self) -> None:
        """Called from the gateway's listener/executor thread.

        Hop to the event loop before touching Home Assistant state or timers.
        """
        self.hass.loop.call_soon_threadsafe(self._handle_push_update)

    @callback
    def _handle_push_update(self) -> None:
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
        """Best-known cover type, preferring the motor's own report.

        Stateless motors never report a blind type, so fall back to the
        device type code from the gateway's device list.
        """
        by_blind_type = BLIND_TYPE_TO_DEVICE_CLASS.get(self._blind.blind_type)
        if by_blind_type is not None:
            return by_blind_type
        return DEVICE_TYPE_TO_DEVICE_CLASS.get(
            self._blind.device_type, CoverDeviceClass.BLIND
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @callback
    def _cancel_pending_stop(self) -> None:
        """Cancel a scheduled end-of-travel stop, if one is pending."""
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None

    async def async_open_cover(self, **kwargs) -> None:
        self._cancel_pending_stop()
        if self.assumed_state:
            self._travel.start_travel(100)
            self._start_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.open)

    async def async_close_cover(self, **kwargs) -> None:
        self._cancel_pending_stop()
        if self.assumed_state:
            self._travel.start_travel(0)
            self._start_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.close)

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop movement. Backs HomeKit's Hold Position."""
        self._cancel_pending_stop()
        if self.assumed_state:
            self._travel.stop()
            self._stop_travel_updates()
            self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._blind.stop)

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move to a target position.

        HA sends 0-100 (0=closed, 100=open); the bridge uses the inverse.
        Motors that report a position are commanded directly. Stateless
        motors are driven for the time it takes to cover the distance and
        then stopped.
        """
        ha_pos: int = kwargs[ATTR_POSITION]
        self._cancel_pending_stop()

        if not self.assumed_state:
            bridge_pos = 100 - ha_pos
            await self.hass.async_add_executor_job(
                self._blind.set_position, bridge_pos
            )
            return

        current = self._travel.current_position()
        distance = ha_pos - current
        # Fully open and fully closed are always sent: HomeKit opens and
        # closes through this method, and the estimate can be wrong (a wall
        # remote, a power cut), so "already there" may not be true. Running
        # a motor into its end stop is harmless and resynchronises the
        # estimate. Only a partial move that is already reached is skipped.
        if abs(distance) < 1 and 0 < ha_pos < 100:
            return

        self._travel.start_travel(ha_pos)
        self._start_travel_updates()
        self.async_write_ha_state()

        if ha_pos >= 100:
            await self.hass.async_add_executor_job(self._blind.open)
            return
        if ha_pos <= 0:
            await self.hass.async_add_executor_job(self._blind.close)
            return

        # Partial move: run in the right direction, then stop on time.
        travel_seconds = abs(distance) / 100.0 * self._travel_time
        if distance > 0:
            await self.hass.async_add_executor_job(self._blind.open)
        else:
            await self.hass.async_add_executor_job(self._blind.close)

        self._unsub_stop = async_call_later(
            self.hass, travel_seconds, self._async_stop_at_target
        )

    async def async_move_for_duration(self, direction: str, duration: float) -> None:
        """Run the motor for a fixed time, then stop.

        The practical way to reach a partial position on motors that cannot
        be commanded to one. The position estimate is advanced by however
        far the blind travelled in that time.
        """
        self._cancel_pending_stop()

        travelled = duration / self._travel_time * 100.0
        current = self._travel.current_position()
        target = current + travelled if direction == "open" else current - travelled
        target = max(0.0, min(100.0, target))

        self._travel.start_travel(target)
        self._start_travel_updates()
        self.async_write_ha_state()

        if direction == "open":
            await self.hass.async_add_executor_job(self._blind.open)
        else:
            await self.hass.async_add_executor_job(self._blind.close)

        self._unsub_stop = async_call_later(
            self.hass, duration, self._async_stop_at_target
        )

    async def _async_stop_at_target(self, _now) -> None:
        """Stop the motor once the estimated travel time has elapsed."""
        self._unsub_stop = None
        self._travel.finalize_if_arrived()
        self._stop_travel_updates()
        try:
            await self.hass.async_add_executor_job(self._blind.stop)
        except Exception as err:
            _LOGGER.warning("Failed to stop blind %s at target: %s", self._blind.mac, err)
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Polling fallback (when multicast push is unavailable)
    # ------------------------------------------------------------------

    async def async_update(self) -> None:
        try:
            await self.hass.async_add_executor_job(self._blind.update)
        except Exception as err:
            _LOGGER.debug("Poll failed for blind %s: %s", self._blind.mac, err)
