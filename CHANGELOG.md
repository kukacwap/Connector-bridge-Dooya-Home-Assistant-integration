# Changelog

## 1.1.0

Reflect a usable state for stateless (open/close-only) blinds so they no
longer always appear "closed" in HomeKit.

- Track an optimistic position driven by whatever last triggered the blind
  (Home Assistant/HomeKit command, automation, or gateway push report)
- Estimate the position over time from a configurable full travel time, so
  HomeKit animates the blind toward its target
- New options flow to set the full open/close travel time per blind
- Report `assumed_state` and restore the last position across restarts
- Motors that report a real position continue to use it unchanged

## 1.0.2

Add a reconfigure flow so the bridge IP or API key can be changed without
removing and re-adding the integration.

- New **Reconfigure** step (integration menu → Reconfigure) to update the
  host/key in place, verified against the live bridge
- Use the gateway MAC address as the entry's unique id so the integration
  survives IP changes; existing entries migrate automatically on startup

## 1.0.1

Stability fix for the gateway dropping off the network.

- Serialize all UDP communication through a single lock so the gateway
  never receives concurrent requests
- Enforce a minimum interval between transmissions to avoid flooding the
  bridge
- Set `PARALLEL_UPDATES = 1` so Home Assistant polls entities one at a time
- Reduce the default fallback polling interval (real-time state comes from
  multicast push)

## 1.0.0

Initial release.

- Config flow (UI) setup with bridge IP and API key
- Cover entities for paired blinds/curtains/shutters/awnings
- Local push updates via multicast, with polling fallback
- Open, close, stop, and set-position support
