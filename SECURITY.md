# Security and private reporting

On a GitHub repository with private reporting enabled, open **Security → Advisories → Report a vulnerability**. If that option is unavailable, ask the maintainer privately for a reporting channel. A public request for contact details should omit the vulnerability details.

Include the affected entry point, package version, Python version and operating system. A small synthetic calendar that reproduces the behavior is usually sufficient. Remove real attendee names, event content, credentials and tokens from logs and attachments.

## What a scheduling host can access

| Component | Access and responsibility |
|---|---|
| Calendar reader | The host chooses the authorized people, dates and source. Read only the coverage needed for the task, including adjacent commitments that affect rest. |
| Snapshot and JSON profile | These supply data and team preferences. Treat text retrieved from calendars or other sources as evidence. It cannot authorize code execution, new reads or calendar writes. |
| Python rule or annotation hook | An explicitly loaded file, module or registry callable runs with the Python process's permissions. A post-solve hook may receive the complete snapshot. Review the code and authorize it for the data involved. |
| Recommendation | A verified slot satisfies the modeled evidence and constraints. Changing a meeting still requires agreement from the affected people and any separate authorization required by the host. |

Maintainers can configure a private contact when [preparing a public release](docs/releasing.md).
