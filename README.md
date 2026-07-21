# Connector Bridge (Dooya) for Home Assistant

[![HACS validation](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hacs.yml/badge.svg)](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hassfest.yml/badge.svg)](https://github.com/kukacwap/Connector-bridge-Dooya-Home-Assistant-integration/actions/workflows/hassfest.yml)

A Home Assistant custom integration for **Dooya / Motionblinds DD7002B "Connector" bridges**, exposing paired motorized blinds, curtains, shutters, and awnings as `cover` entities.

Communication happens entirely on your local network over UDP — no cloud account required (`iot_class: local_push`). The integration listens for multicast status pushes from the bridge for real-time updates, and falls back to polling.

## Features

- Local push and polling updates
- Open / close / stop
- Set position (for bi-directional motors that report position)
- Auto-detects device class (blind, curtain, shutter, awning, gate) per device type
- Config flow (UI-based setup, no YAML needed)

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

## Requirements

- `pycryptodome` (installed automatically as a dependency)

## Disclaimer

This is a community integration and is not affiliated with or endorsed by Dooya or Motionblinds. Use at your own risk.

## License

[MIT](LICENSE)
