# Changelog

## 1.3.1

Test suite and the bugs it found.

- **Brand icon** (`custom_components/connector_bridge/brand/`), so HACS stops
  reporting the repository as having no brand assets.
- **Release workflow**: pushing a `V*` tag now publishes the matching GitHub
  release from this file, after checking the tag against `manifest.json`.
  HACS offers releases, not branch commits, so a merge alone never reached
  anyone as an update.

- **Unit tests** (`tests/`, 169 of them) covering the UDP protocol layer, the
  position estimator, both flows, both platforms and diagnostics, run on every
  push by a new `Tests` workflow. Each fix below has a test that fails without it.
- **Fixed: a malformed UDP packet aborted the command that received it.**
  Anything on the LAN can reach the gateway socket, and the bridge itself
  occasionally truncates a reply; the resulting `JSONDecodeError` escaped as
  a service-call failure. Unparseable packets are now skipped.
- **Fixed: a network error bypassed IP recovery.** A bridge moved to another
  subnet answers with `OSError` rather than silence, which skipped the
  rediscovery path and left the connectivity sensor reporting a bridge that
  was gone. Unreachable is now treated like silent: retried, then surfaced
  as a timeout so the bridge is looked for by MAC.
- **Fixed: blinds were named twice** ("Blind 0001 Blind 0001"). The cover is
  its device's primary entity and now takes the device name. Existing entity
  ids are unaffected; only the displayed name changes.
- **Fixed: diagnostics leaked every blind's MAC address** through the stored
  travel time map, which is keyed by MAC — despite the rest of the report
  deliberately using indexes to avoid exactly that. Only the number of
  configured travel times is reported now; the values remain per blind.
- **Fixed: a wrong API key reported "Unexpected error. Check the logs."**
  The key is used directly as an AES-128 key, so a length other than 16
  characters failed deep inside the crypto layer. Length is now checked up
  front and reported as an invalid key.
- **Fixed: the options screen discarded travel times** for blinds the gateway
  did not list at that moment. Saved values are now merged, not replaced.
- **Fixed: the Hungarian translation never reached anyone.** `strings.json`
  held the Hungarian text, which Home Assistant reads as the English source,
  and there was no `translations/hu.json`. The Hungarian strings now ship
  where they are loaded from, and `strings.json` is English again.
- **Fixed: `datetime.utcnow()`**, deprecated and scheduled for removal, used
  to build every message id.
- Hardened a stored travel time of zero (division by zero when converting a
  distance to seconds) and a multicast listener that could spin on a CPU
  core if it ever lost its socket.

## 1.3.0

Automation support.

- **Movement events**: blinds now fire a `connector_bridge_blind_moved` event
  carrying a `source` of `homeassistant` or `external`, so automations can be
  triggered by a physical remote. Reports arriving shortly after a command we
  sent are attributed to us; only genuine changes are announced.
- **`connector_bridge.move_for_duration` service**: run a blind in one
  direction for a set time, then stop. The practical way to reach a partial
  position on motors that cannot be sent to one; the position estimate is
  advanced accordingly.
- **Sun-based shading blueprint** (`blueprints/automation/connector_bridge/`),
  importable from the repository. Shades a window while the sun is on it and
  it is warm, then restores afterwards. Handles azimuth ranges that wrap past
  north, applies temperature hysteresis, and skips covers already near the
  target so the bridge is not sent redundant commands.
- Every message pushed by the gateway is now logged at debug level, which
  makes it possible to confirm what the hardware reports for remote-initiated
  moves.

## 1.2.0

Resilience and diagnostics.

- **Fixed dependency**: the integration imports `Cryptodome` but declared
  `pycryptodome` (which provides `Crypto`). It now requires
  `pycryptodomex`, so a clean install no longer depends on another
  integration happening to pull that package in.
- **Automatic IP recovery**: when the bridge stops answering at its stored
  address it is located again on the network by MAC and the config entry is
  updated, with a repair issue suggesting a DHCP reservation.
- **Discovery onboarding**: the bridge is found via DHCP and via multicast,
  so setup pre-fills the address and only the API key needs entering.
- **Diagnostics**: download diagnostics from the integration page; the API
  key and network identifiers are redacted.
- **Repairs**: an issue is raised when multicast push updates are
  unavailable and the integration falls back to polling.
- **Gateway connectivity sensor**: a diagnostic binary sensor reporting
  whether the bridge is reachable, with last-seen and push-status details.
- **Human-readable model names** for the gateway and blinds instead of raw
  device type codes.
- **HomeKit**: position control is now offered for stateless blinds too
  (driven for a calculated time), which is what lets HomeKit show a
  position slider and Hold Position. Cover type now falls back to the
  device type code, so curtains/awnings report correctly for Siri.
- Fixed push updates being applied from a non-event-loop thread.

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
