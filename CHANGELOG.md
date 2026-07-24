# Changelog

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
