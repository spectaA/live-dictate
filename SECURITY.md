# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/spectaA/live-dictate/security/advisories/new). Do not open a public issue.

You can expect a first answer within a week.

## Scope

live-dictate listens to the keyboard, records the microphone while the hotkey is held and sends audio and text to OpenAI. Reports about the handling of the API key, audio, transcripts, the clipboard or the update mechanism are especially welcome.

## Verifying releases

Each release ships a `SHA256SUMS` file and a [build provenance attestation](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations). To check that a package was built by this repository's release workflow:

```bash
gh attestation verify live-dictate_*_amd64.deb --repo spectaA/live-dictate
```
