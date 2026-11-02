# Blackmagic SmartView / SmartScope for Home Assistant

[![Ruff](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/ruff.yml)
[![Hassfest](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hassfest.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hassfest.yml)
[![HACS](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hacs.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/hacs.yml)
[![CodeQL](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/codeql.yml)
[![Tests](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-smartview-ha/actions/workflows/tests.yml)
[![Quality scale: Bronze](https://img.shields.io/badge/quality%20scale-Bronze%20aligned-cd7f32.svg)](#quality-scale)
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
| Model, Protocol version | sensor |
| Hostname (disabled by default: it contains the MAC address) | sensor |
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
by itself. Names are up to 64 printable characters; line breaks are refused.
A new rename is refused for 30 seconds, while the monitor restarts its network.

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
monitor sends, including fields this integration does not use yet. IP addresses
and the hostname are redacted and MAC addresses are masked, so the file can be
attached to a public issue. Debug logs mask MAC addresses too.

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

## Removal

Settings → Devices & services → *Blackmagic SmartView / SmartScope* →
three-dot menu on the entry → **Delete**. To remove the code as well, uninstall
the repository in HACS and restart Home Assistant. Nothing is changed on the
monitor itself.

## Security

The SmartView protocol has no authentication or encryption: anyone who can
reach TCP port 9992 can change tally, scopes and picture settings, rename the
monitor or change its network settings. **The network is the real protection.**

Recommended network setup:

- Put the monitor in an AV or management VLAN, with a DHCP reservation.
- Firewall: allow only Home Assistant (and the computer running Blackmagic
  SmartView Setup) to reach the monitor on TCP 9992; block everything else into
  that VLAN.
- Block internet access for the monitor. It does not need any.
- No port forwarding and no UPnP.
- Discovery uses mDNS. Across VLANs it needs an mDNS reflector; entering the IP
  address by hand works just as well.

What the integration adds itself:

- Values never contain line breaks or control characters, so a name or setting
  can never add commands (no command injection). Host names are validated.
- Data from the monitor is treated as untrusted: line length, block size,
  number of blocks, number of monitors (A to H) and value length are limited, so
  a faulty or malicious device cannot make memory grow.
- Updates pushed by the monitor are bundled to at most ten per second, and an
  error in one entity never breaks the connection.
- One connection, commands one at a time, reconnects with back-off: the
  integration never floods the monitor.
- No listening ports, no external Python packages, nothing stored but host,
  port and the model, name and monitor count for offline starts.
- CI runs with read-only permissions; GitHub's own actions are pinned to commit
  hashes and kept current by Dependabot.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Quality scale

Aligned with the Home Assistant quality scale: Bronze. Home Assistant only
grades core integrations, so this is a self-assessment, recorded rule by rule
in [`quality_scale.yaml`](custom_components/blackmagic_smartview/quality_scale.yaml).

One Bronze rule is deliberately exempt: *test-before-setup*. That rule wants
setup to fail when the device cannot be reached, which would turn every
switched-off studio into a "Failed to set up" error. The connection is tested
in the config flow and in reconfigure instead.

The test suite covers the config flow (user, discovery and reconfigure), setup
with the monitor on and off, all controls, diagnostics redaction, and the
protocol client against a simulated monitor, including hostile input.

## License

MIT — see [LICENSE](LICENSE).
