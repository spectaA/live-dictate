# Contributing

Thanks for your interest in live-dictate. Bug reports, ideas and pull requests are welcome.

## Before you start

For anything larger than a small fix, please open an issue first so we can agree on the approach.

## Development setup

live-dictate uses [uv](https://docs.astral.sh/uv/) and the distribution's GTK bindings:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 pulseaudio-utils xdotool xclip x11-utils xvfb
uv venv --system-site-packages --python /usr/bin/python3
uv sync
uvx pre-commit install   # optional: run ruff on every commit
```

## Checks

Pull requests must pass the same checks as CI:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest            # or `xvfb-run uv run pytest` without a display
```

After a change to the overlay, regenerate the screenshots used by the README:

```bash
uv run python scripts/render_overlay.py docs/assets listening live-text locked removing pasted
```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`, `fix: ...`, `docs: ...`, `ci: ...`.
Explain why in the body, not what: the diff already shows what changed.

## Releasing

1. Update `__version__` in `src/live_dictate/__init__.py`.
2. Move the `Unreleased` entries of `CHANGELOG.md` to a new version section.
3. Commit, then tag and push:

   ```bash
   git tag -a v1.2.3 -m "v1.2.3"
   git push origin main v1.2.3
   ```

The release workflow runs CI, builds the Debian package, attests its provenance and publishes a GitHub release with the changelog section as notes.

To build the package locally, install [nfpm](https://nfpm.goreleaser.com/) and run `packaging/build-deb.sh`.
