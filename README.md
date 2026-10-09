# Blackmagic SmartView / SmartScope for Home Assistant

[![Ruff](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/ruff.yml)
[![Hassfest](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hassfest.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hassfest.yml)
[![HACS](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hacs.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hacs.yml)
[![CodeQL](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Videobarista/blackmagic-smartview-ha)](https://github.com/Videobarista/blackmagic-smartview-ha/releases)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Videobarista&repository=blackmagic-smartview-ha&category=integration)

Home Assistant custom integration for Blackmagic Design SmartView and
SmartScope monitors, using the Blackmagic SmartView Ethernet Protocol (v1.4)
on TCP port 9992.

Developed for a **SmartScope Duo 4K**. SmartView Duo, SmartView HD and
SmartView 4K use the same protocol and should work too; the integration only
creates the entities a model supports.

## Status

Running against a SmartScope Duo 4K (protocol version 1.3).

## Why local push

The monitor sends a full state dump on connect and pushes changes after that.
The integration holds one persistent connection and never polls.

What the SmartScope Duo 4K does in practice, and how the integration deals
with it:

- It acknowledges a setting (`ACK`) but does not report the new value back to
  the client that changed it. The integration therefore shows an acknowledged
  setting straight away. Anything the device reports later still wins.
- It does not support a protocol keep-alive (`PING` is answered with a syntax
  error). A switched-off monitor is detected with TCP keepalive instead.
- It answers every setting with `ACK`. No answer within three seconds means the
  monitor is not reachable, and Home Assistant shows an error instead of
  pretending the setting was applied.
- Right after a connection closes, for example while the integration reloads,
  it can refuse a new connection for a moment. The integration retries a few
  times before giving up, and then keeps retrying in the background.

## Installation

HACS → Custom repositories → add this repository URL → category
**Integration** → install → restart Home Assistant → Settings → Devices &
services → Add integration → *Blackmagic SmartView / SmartScope*.

Enter the IP address of the monitor. Port 9992 is the default. Monitors that
advertise themselves on the network are also discovered automatically.

## Changing the IP address

Settings → Devices & services → *Blackmagic SmartView / SmartScope* →
three-dot menu on the entry → **Reconfigure**. The new address is only saved
once the monitor answers on it. Entities, history, dashboards and automations
all stay as they are.

## When the monitor is switched off

Switching off the studio is a normal situation, not an error:

- Short network hiccups are ignored. Only when the monitor has not answered for
  about a minute is it considered offline.
- Then the **Connection** sensor turns off and all other entities become
  unavailable. One line is logged at info level; nothing is logged as a warning
  or error.
- The integration keeps trying to reconnect in the background, at most once a
  minute, and picks the monitor up by itself when it comes back.
- Home Assistant also starts normally while the monitor is off. The model and
  number of monitors are remembered from the last connection, so all entities
  are there, just unavailable until the monitor is reachable.

## Entities

Each LCD is a monitor: **A** (left) and **B** (right) on a Duo.

### Per monitor

| Entity | Type | Models |
| --- | --- | --- |
| Monitor A scope | select | SmartScope Duo 4K |
| Monitor A audio channels | select | SmartScope Duo 4K |
| Monitor A tally | switch | all |
| Monitor A tally color | select | all |
| Monitor A brightness | number (0–255) | all |
| Monitor A contrast, saturation | number (0–255, 127 = neutral) | not SmartView 4K |
| Monitor A SD aspect ratio | select (16:9 / 4:3) | Duo, HD, SmartScope |
| Monitor A LUT, input | select | SmartView 4K |
| Monitor A identify | button | all |

Scope options: picture (video monitor), waveform (luma), vectorscope 100% and
75%, RGB parade, YUV parade, histogram, audio dBFS and audio dBVU. The audio
channels select picks the pair shown on the audio meters (1 & 2 up to 15 & 16).

### Device

| Entity | Type |
| --- | --- |
| Connection | binary sensor (stays available when the monitor is off) |
| DHCP, Mounted inverted | binary sensor |
| IP address (netmask, gateway and static settings as attributes) | sensor |
| Hostname, Model, Protocol version | sensor |
| Device name | text |

## Tally

The **tally** switch shows or hides the soft tally border. Switching it on uses
the last colour that was active on that monitor, red by default. Pick the colour
with **tally color**: red, green, blue, white or none.

```yaml
action: switch.turn_on
target:
  entity_id: switch.smartscope_monitor_a_tally
```

```yaml
action: select.select_option
target:
  entity_id: select.smartscope_monitor_b_tally_color
data:
  option: green
```

Entity IDs start with the device name your monitor reports, so yours may differ.

A tally signal on the DB-9 port always overrides the soft tally. The switch then
follows what is actually on screen.

## Identify

Shows a white border around the picture for 15 seconds, to find the right
monitor in a rack. It temporarily overrides any tally border.

## Renaming the device

Changing **Device name** makes the monitor restart its network and advertise
the new name. The connection drops for a moment and the integration reconnects
by itself.

## Network settings

DHCP and static IP are shown, not changed. One wrong value would make the
monitor unreachable from Home Assistant, so change them in Blackmagic SmartView
Setup instead.

## Troubleshooting

Add this to `configuration.yaml` to see the protocol traffic:

```yaml
logger:
  logs:
    custom_components.blackmagic_smartview: debug
```

**Download diagnostics** on the integration entry shows the full state dump the
monitor sends, including fields this integration does not use yet.

If the monitor rejects a setting (NAK), Home Assistant shows an error and the
entity does not move. Unreachable monitors log at debug level on purpose: a
switched-off monitor is normal, not an error worth filling your log with.

## Brand images

The integration ships its own icon in
`custom_components/blackmagic_smartview/brand/`, which Home Assistant 2026.3
and later picks up automatically. It is an original, generic design (a dual
monitor showing a waveform and a tally border), not the Blackmagic Design logo.
Replace `icon.png` (256x256) and `icon@2x.png` (512x512) with your own artwork
if you prefer, then restart Home Assistant.

## Security

The protocol has no authentication or encryption. See [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
