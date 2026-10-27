# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-27

### Added

- Push-to-talk dictation on X11 with a configurable hotkey, in hold or toggle mode.
- Live transcription with OpenAI `gpt-live-transcribe` over the Realtime API.
- Cleanup of filler words, stutters and false starts with `gpt-6-luna` in Fast mode, skipped for dictations under five words.
- Overlay with a live audio level meter, shown on the monitor under the pointer.
- Paste into the focused window, with `Ctrl+Shift+V` in terminals and clipboard restoration.
- `live-dictate check` to verify tools, API key, microphone and the cleanup call.
- `live-dictate update` to install the latest release with checksum verification.
- Debian package with a systemd user service, built and published by GitHub Actions.

[Unreleased]: https://github.com/spectaA/live-dictate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/spectaA/live-dictate/releases/tag/v0.1.0
