# Architecture

## Lifecycle of a dictation

1. `pynput` sees the hotkey go down.
2. `parec` starts and streams mono PCM16 at 24 kHz from the default PipeWire or PulseAudio source.
3. A WebSocket opens to `wss://api.openai.com/v1/realtime?intent=transcription`. Audio captured while the connection opens waits in a queue, so nothing is lost.
4. Each 40 ms chunk is sent as an `input_audio_buffer.append` event.
5. `conversation.item.input_audio_transcription.delta` events update the overlay while the user speaks.
6. When the hotkey goes up, `parec` stops, the client sends `input_audio_buffer.commit` and waits for the `completed` event.
7. Transcripts of five words or more go to the Responses API for cleanup.
8. The text is placed on the clipboard with `xclip` and pasted with `xdotool key ctrl+v`. The previous clipboard is restored.

## Threads

| Thread | Responsibility |
|---|---|
| Main | GTK main loop and the overlay window |
| `asyncio` | Dictation state machine, WebSocket, HTTP calls |
| `pynput` | X11 keyboard listener (RECORD extension) |
| `recorder` | Reads the output of `parec` |

The `pynput` and `recorder` threads hand work to the asyncio loop with `loop.call_soon_threadsafe`. The asyncio thread updates the overlay with `GLib.idle_add`.

## Design decisions

- **`gpt-live-transcribe` without turn detection.** The model emits text while the user speaks. One key press is one turn, committed on release.
- **`parec` instead of PortAudio.** It ships with every PipeWire or PulseAudio desktop, so no native audio library has to be bundled.
- **A `POPUP` GTK window.** It is override-redirect, so it never takes the focus and the paste reaches the right window.
- **Paste through the clipboard.** `xdotool type` mangles non-ASCII characters and is slow on long text.
- **Cleanup with `reasoning.effort = none` and the Fast service tier.** The task is simple, and latency matters more than reasoning.
- **Fall back to the raw transcript.** A failed or slow cleanup must never lose a dictation.
- **Save before pasting.** The transcript is written to `recent.json` as soon as it is final, then updated with the cleaned text and the outcome. Cleanup and paste can fail, and the overlay gear starts a throwaway dictation, so the file keeps 10 entries and never saves a dictation without text.
- **Trim the overlay by whole lines.** Dropping leading lines keeps the remaining lines wrapped exactly as before, so the text scrolls up instead of shifting sideways.
- **50 ms grace period on key release.** X11 auto-repeat sends release and press pairs while a regular key is held. Modifier keys such as Ctrl are not affected.
- **The overlay card is drawn with cairo, not CSS.** Cairo has no conic gradient, so the ring paints colored slices, rendered once per frame into an opaque group to avoid seams, then clipped to the ring. The glow stacks translucent bands, since GTK 3 cannot blur behind a window.
- **The window margin contains the whole glow.** The window is cropped to the card margin. A test checks that the glow fades out before the window edges.
- **Removed words come from a diff.** The cleanup model returns plain text, so the overlay compares raw and clean words with `difflib`, ignoring case and punctuation, to strike out what was dropped. The paste does not wait for this animation.
- **Settings apply by re-executing the daemon in place.** The overlay's gear opens the settings window inside the daemon. Saving re-executes the process with `os.execv`, so systemd keeps the same PID and the single-instance lock, opened close-on-exec, is released for the new image.
- **History as append-only JSONL.** One line per dictation is easy to read with `jq` or a spreadsheet, and a corrupted line never spoils the others. The file is created with mode `0600`, and pruning rewrites it atomically.
- **Accent color from GNOME 47+ only.** GTK theme colors are unreliable: Pop's theme names a cyan color `accent_color` while it looks orange. Without a configured or GNOME accent, the ring stays neutral gray.

## Packaging

The Debian package targets distributions based on Ubuntu 22.04 with Python 3.10:

- Python dependencies are installed from `uv.lock`, with hash checking, into `/opt/live-dictate/lib`. They include compiled extensions for the Python 3.10 ABI.
- GTK, cairo and their Python bindings come from the distribution, which is why they are not on PyPI's dependency list.
- `/usr/bin/live-dictate` runs `/usr/bin/python3 -s -m live_dictate` with `PYTHONPATH` set to the bundled libraries.
- A systemd user unit starts the daemon with the graphical session. `postinst` enables it with `systemctl --global`, because maintainer scripts run as root and cannot reach a user's session.
- Per-user state (API key in the keyring, `~/.config/live-dictate/config.toml`) is created by the application, never by the package. On first launch without a key, the daemon opens the setup window instead of failing.
- On upgrade, `postinst` restarts the daemon of every logged-in user; on removal, `prerm` stops it. Otherwise running daemons would keep executing deleted code.
