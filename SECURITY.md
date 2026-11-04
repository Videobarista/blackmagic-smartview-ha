# Security Policy

## Supported versions

Only the most recent release receives security fixes.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Please report security issues privately through GitHub's
[security advisory form](https://github.com/Videobarista/blackmagic-smartview-ha/security/advisories/new)
rather than in a public issue.

Expect an initial response within 14 days. This is a hobby project maintained
in spare time, so please allow reasonable time for a fix before any public
disclosure.

## Scope

This integration talks to a SmartView or SmartScope over the Blackmagic
SmartView Ethernet Protocol on TCP port 9992, and to an Audio Monitor over the
Blackmagic Audio Monitor Ethernet Protocol on TCP port 9996. Relevant to note:

- **The protocols have no authentication or encryption.** Anyone able to reach
  port 9992 or 9996 can read and change the device configuration. Keep video equipment
  on a management VLAN that is not routed to the internet.
- The integration stores only the host and port in the Home Assistant config
  entry, plus the device type, model, name and monitor count so it can start
  while the device is off. No credentials are involved, because the protocol has none.
- Connections are outbound only. The integration opens no listening ports.
- Outgoing values are checked for line breaks and control characters, so they
  cannot inject commands. Data from the device is size-limited and treated as
  untrusted.
- Diagnostics redact IP addresses, the hostname and the Audio Monitor's Unique
  ID, and mask MAC addresses.
- The CI workflows run with read-only permissions. GitHub's own actions are
  pinned to commit hashes; `hacs/action` and Home Assistant's `hassfest` follow
  their main branches as their maintainers recommend.

See the Security section of the [README](README.md#security) for the
recommended network setup.

Vulnerabilities in the SmartView or Audio Monitor firmware itself belong to Blackmagic Design,
not to this repository.
