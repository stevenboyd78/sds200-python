# Security Policy

## Supported versions

Until the first stable release, security fixes are applied to the default branch
and the newest published prerelease only.

| Version | Supported |
| --- | --- |
| Default branch | Yes |
| Latest prerelease | Yes |
| Older development snapshots | No |

## Network-control security

The SDS200-only virtual serial network protocol uses unauthenticated, unencrypted UDP
traffic. Anyone who can reach the scanner's control port may be able to send
commands or observe responses.

- Keep the scanner on a trusted LAN.
- Use firewall rules to limit access.
- Use a secured VPN for remote access.
- Do not forward UDP port `50536` directly from the public Internet.
- Treat traces and debug logs as potentially sensitive.
- Do not embed public scanner addresses or private network credentials in issues.

Network audio, when implemented, will remain separate from control transport and
will require its own threat review.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose users or enable
unauthorized scanner control.

Use GitHub private vulnerability reporting when it is enabled for the
repository. Otherwise, contact the maintainer through the GitHub profile and
request a private reporting channel without posting exploit details publicly.

Please include:

- Affected version or commit
- Transport and platform
- Impact
- Reproduction steps or proof of concept
- Suggested mitigation, when known
- Whether the report may be credited publicly

You should receive an acknowledgment when the report is reviewed. Because this
is a volunteer project, no guaranteed response or remediation time is promised.

## Safety notice

This project is not designed or certified for emergency, life-safety, dispatch,
or public-warning use. Do not rely on it as the sole means of receiving urgent
communications.
