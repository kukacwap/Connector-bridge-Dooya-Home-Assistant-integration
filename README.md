# Connector Bridge (Dooya) for Home Assistant

[![HACS validation](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hacs.yml/badge.svg)](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hassfest.yml/badge.svg)](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hassfest.yml)

A Home Assistant custom integration for **Dooya / Motionblinds DD7002B "Connector" bridges**, exposing paired motorized blinds, curtains, shutters, and awnings as `cover` entities.

Communication happens entirely on your local network over UDP — no cloud account required (`iot_class: local_push`). The integration listens for multicast status pushes from the bridge for real-time updates, and falls back to polling.

## Features

- Local push and polling updates
- Open / close / stop / set position
- Position estimation for stateless motors that don't report one, so state
  shows correctly in HomeKit (configurable travel time per blind)
- Automatic discovery, and automatic recovery if the bridge's IP changes
- Gateway connectivity sensor, diagnostics download, and repair notices
- Auto-detects device class (blind, curtain, shutter, awning, gate) per device type
- Config flow (UI-based setup, no YAML needed), plus reconfigure and options flows

## Supported hardware

- Dooya DD7002B "Connector" WiFi bridge (and rebrands using the same UDP protocol, e.g. Motionblinds)
- Blinds, TDBU (top-down/bottom-up), sunblinds, WiFi blinds/curtains paired to the bridge

## Installation

### HACS (recommended)

1. In Home Assistant, go to **HACS → Integrations → ⋮ → Custom repositories**.
2. Add this repository URL with category **Integration**.
3. Search for **Connector Bridge (Dooya)** and install it.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/connector_bridge` folder from this repository into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

Configuration is done entirely through the Home Assistant UI:

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Connector Bridge (Dooya)**.
3. Enter:
   - **Bridge IP address** — the local IP of your Connector bridge.
   - **API key** — found in the Connector+ app under **Settings → About → tap 5 times anywhere**.

The integration will connect to the bridge, discover paired blinds, and add them as `cover` entities.

If the bridge is on your network, its address is discovered automatically and
pre-filled, so usually only the API key needs entering.

### Blind travel times

Motors that don't report their own position are positioned by running them
for a calculated time. Time how long a full open (or close) takes, then set
it per blind under **Configure** on the integration. Without this, position
estimates and the HomeKit slider will be inaccurate.

### If the bridge IP changes

The integration finds the bridge again by MAC address and updates itself, so
a DHCP address change is handled automatically. You can also change the
address manually via **Reconfigure**. Assigning the bridge a static IP or a
DHCP reservation is still recommended.

## Troubleshooting

- **Gateway connectivity sensor** — a diagnostic entity showing whether the
  bridge is reachable, when it was last seen, and whether push updates are active.
- **Diagnostics** — download from the integration page (the API key and
  network identifiers are redacted) when reporting an issue.
- **Push updates unavailable** — if a repair notice reports this, multicast
  traffic isn't reaching Home Assistant. This is common with Docker bridge
  networking or when the bridge is on another VLAN/subnet; the integration
  falls back to polling in the meantime.

## Requirements

- `pycryptodomex` (installed automatically as a dependency)

## Disclaimer

This is a community integration and is not affiliated with or endorsed by Dooya or Motionblinds. Use at your own risk.

## License

[MIT](LICENSE)
