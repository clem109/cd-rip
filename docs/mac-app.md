# Mac app

CD Rip.app is a native SwiftUI window with a menu-bar icon. It bundles the Python
engine and starts idle. Closing the window keeps the watcher available in the menu bar.

The resizable album view shows artwork, edition details, track durations, lyrics availability,
per-track status and extraction progress. It restores the most recently saved album when
opened. Toolbar buttons open the archive, activity log and settings; rip controls stay below
the track list. Progress measures audio extraction, while Music import has its own status.

## Build

Requires macOS 13+, Xcode Command Line Tools, Python 3.11+, and Homebrew.

```sh
brew install ffmpeg libdiscid libcdio-paranoia
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mac]'
.venv/bin/python mac/build.py
open 'dist/CD Rip.app'
```

The build targets the current Mac's architecture. It is locally signed, not notarized
for distribution. FFmpeg and the CD-reading libraries remain Homebrew dependencies;
Python and Mutagen are bundled. A distributable download would additionally need dependency
licensing/source notices, Developer ID signing, and notarization.

## Use

1. Set Music's CD insertion setting to **Show CD**.
2. Open **Settings** from the toolbar and click **Check Setup**. Choose your output format.
3. Click **Start watching**, then insert a CD. Use **Rip inserted CD** for an idle disc
   already in the drive; don't use it while another app is ripping.
4. Pick the matching edition when prompted. Completed albums are added to Music by default.

Artwork and lyrics download in the background during extraction. Artwork appears as soon
as it is available. Tags are embedded after audio verification and before Music import.

**Stop after this CD** completes the current extraction, tagging, and import before stopping.
Quitting while active uses the same graceful stop. It can take time if a disc or metadata
provider is slow. Closing the window alone does not stop work.

Use **Settings → Choose…** to select the archive folder and **Open Archive** in the toolbar to reveal it.
The activity-log button shows read errors and missing-metadata notices. Failed metadata can
be retried using the CLI commands in [Usage](usage.md).

The app remembers its archive and format preferences. The default is `~/Music/CD Rip/`.
Only one packaged ripper can run at a time. An opt-in `--local-config` build also detects
the checkout's old archive and respects the original CLI lock; do not distribute that build.

ALAC is the default. AAC exposes 128, 192, 256 and 320 kbps quality options, with 256 as
the default. FLAC disables Music import without erasing your preference for other formats.
WAV supports ID3 metadata, but other players may ignore those tags. Each format (and AAC
bitrate) gets a separate archive directory, so switching does not overwrite another rip.

The native app handles Music imports and may prompt for Automation permission. Each track
is marked imported only after Music confirms it. Import failures remain visible as **Needs
attention** and are saved in the album's job record; uncertain imports are not blindly retried.
Check setup never reads a CD
or controls Music. Edition prompts use JSON messages over pipes, not Terminal interaction.
