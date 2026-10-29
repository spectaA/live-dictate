# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-29

### Added

- Shortcuts tab in the settings. Every key can be changed: dictate, lock while holding, finish and cancel. A shortcut can be one key, a modifier alone or a combination.
- The Shortcuts tab warns when two shortcuts overlap, and when another app already uses one, such as a launcher on Ctrl+Space.
- Dictionary tab in the settings, for words that must be spelled exactly. The cleanup now receives the list too, not only the transcription.
- `lock_key`, `finish_key` and `cancel_key` settings.

### Changed

- The hotkey and the mode moved from the General tab to the Shortcuts tab.
- A hotkey with a regular key, such as F9, no longer reaches the focused app.
- The overlay shows the configured keys.
- Saving refuses a configuration the daemon could not load.

## [0.5.1] - 2026-10-28

### Fixed

- `live-dictate update` prints a clear message instead of a traceback when the password dialog is closed or the install fails.

## [0.5.0] - 2026-10-27

### Added

- Lock mode: double-tap the hotkey, or press Space while holding it, to keep dictating hands-free. Enter or the hotkey finishes, Esc cancels. Enter and Esc never reach the focused app while locked.
- `max_lines` setting, also in the Appearance tab, for the number of live text lines.

### Changed

- Redesigned overlay: line icons, a tinted state disc, a level meter, a timer, keycaps for Space, Enter and Esc, and a progress bar during cleanup.
- Errors show a short title, the detail and a Settings button when the key or the network is the cause.

## [0.4.1] - 2026-10-27

### Added

- The last 10 dictations are saved on disk before the cleanup and the paste, even with the history off. A cancelled or failed dictation keeps the text shown live.
- Recent tab in the settings window to copy a saved dictation back.
- `live-dictate last [N]` prints a saved dictation.

### Fixed

- Long dictations lost their cleanup: the cleanup timeout now grows with the number of words.
- The previous clipboard is restored later after a long paste, so slow apps no longer paste the old clipboard.

## [0.4.0] - 2026-10-27

### Added

- Settings window with General, Cleanup, Appearance and History tabs.
- Gear button in the overlay that opens the settings; the dictation in progress is cancelled.
- Opt-in local history of dictations with retention, statistics, a per-day chart and the latest dictations in the History tab.
- `live-dictate stats` to summarize the history from the terminal.
- Cleanup styles: Off, Light, Standard and Polished.

### Changed

- Rewritten cleanup prompts with examples. Standard, the default, fixes slips and self-corrections without rewording.
- The overlay's transparent glow margin no longer blocks clicks on the windows below.

### Fixed

- `live-dictate setup` could fail to start when GTK 4 was also installed.
- `live-dictate update` no longer prints `debconf` warnings.

## [0.3.0] - 2026-10-27

### Added

- Accent color for the overlay ring, picked in the setup window with a live preview, or read from the GNOME 47+ accent setting.
- "Pasted" confirmation at the end of each dictation.

### Changed

- New overlay design: a rotating gradient ring with a glow that follows your voice.
- New words fade in, and the words removed by the cleanup are struck out before the text is pasted.

## [0.2.0] - 2026-10-27

### Added

- Setup window to enter and verify the OpenAI API key and pick the language and hotkey, opened on first launch and with `live-dictate setup`.
- Applications menu entry that opens the setup window.

### Changed

- The package enables the service for every user, so live-dictate starts at login without any command.
- Upgrading the package restarts the daemon of logged-in users, and removing it stops the daemon.
- The default language follows the system locale.

### Fixed

- HTTP request lines no longer clutter the output of `live-dictate update` and the service logs.

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

[Unreleased]: https://github.com/spectaA/live-dictate/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/spectaA/live-dictate/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/spectaA/live-dictate/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/spectaA/live-dictate/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/spectaA/live-dictate/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/spectaA/live-dictate/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/spectaA/live-dictate/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/spectaA/live-dictate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/spectaA/live-dictate/releases/tag/v0.1.0
