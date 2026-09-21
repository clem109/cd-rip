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

`docs/ci.yml` is a GitHub Actions template for macOS checks on Python 3.11 and 3.14.
To enable it, move it to `.github/workflows/ci.yml` and push using credentials with
permission to update workflows.
