# Development

```sh
brew install python ffmpeg libdiscid libcdio-paranoia
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m build
```

Tests use generated audio and mocked drive, network, and Music interactions. They never
access a physical CD. FFmpeg is required for the real encoding/tagging integration tests.

The CLI lives in `src/cdrip/cli.py`. `python -m cdrip` and the installed `cd-rip`
command share the same entry point. Runtime data stays outside the installed package.

Do not commit audio, artwork, lyrics, job files, logs, or virtual environments.

`.github/workflows/ci.yml` runs macOS checks on Python 3.11 and 3.14 and verifies a
native app build. Actions are pinned to immutable commits; PR jobs have read-only access.

For Developer ID signing and notarization, see [Distribution](docs/distribution.md).
Contributions are made under the project's GPL-3.0-or-later license.
