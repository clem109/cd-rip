# cd-rip

Audio CDs → Apple Lossless → Music.

A small macOS CLI that rips CDs, embeds artwork and available lyrics, ejects the disc,
and adds the album to Music.

## Install

Requires macOS, Homebrew, and Python 3.11+.

```sh
brew install pipx ffmpeg libdiscid libcdio-paranoia
pipx ensurepath
git clone https://github.com/clem109/cd-rip.git
cd cd-rip
pipx install .
```

Open a new terminal after `pipx ensurepath` if `cd-rip` is not found.

## Run

In Music, set **Settings → General → When a CD is inserted → Show CD**.

```sh
cd-rip doctor
cd-rip watch
```

Insert a CD. Albums are saved in `~/Music/CD Rip/` and added to Music automatically.
The watcher ignores discs already inserted at startup; use `cd-rip rip` for those.
Press Ctrl-C to stop.

```sh
cd-rip rip                         # Rip the inserted CD
cd-rip status                      # Show saved progress
cd-rip watch --output /path/to/music
cd-rip watch --no-music --no-lyrics
```

Read correction uses `cd-paranoia`; decoded audio is checked after ALAC encoding.
AccurateRip verification is not implemented. Artwork and lyrics depend on provider coverage.

[Recovery and configuration](docs/usage.md) · [Development](CONTRIBUTING.md)
