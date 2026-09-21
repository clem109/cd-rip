# Mac app

CD Rip.app is a native SwiftUI window with a menu-bar icon. It bundles the Python
engine and starts idle. Closing the window keeps the watcher available in the menu bar.

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
2. Open the app and click **Check setup**.
3. Click **Start watching**, then insert a CD. Use **Rip inserted CD** for an idle disc
   already in the drive; don't use it while another app is ripping.
4. Pick the matching edition when prompted. Completed albums are added to Music by default.

Artwork and lyrics download in the background during extraction. Artwork appears as soon
as it is available. Tags are embedded after audio verification and before Music import.

**Stop after this CD** completes the current extraction, tagging, and import before stopping.
Quitting while active uses the same graceful stop. It can take time if a disc or metadata
provider is slow. Closing the window alone does not stop work.

Use **Change…** to select the archive folder and **Open library** to reveal it.
The activity-log button shows read errors and missing-metadata notices. Failed metadata can
be retried using the CLI commands in [Usage](usage.md).

The app remembers its archive preference. A local build detects a pre-existing `library/`
folder and uses it initially; otherwise the default is `~/Music/CD Rip/`.
Only one packaged ripper can run at a time. Local builds also respect the original CLI lock.

The first Music import may prompt for Automation permission. Check setup never reads a CD
or controls Music. Edition prompts use JSON messages over pipes, not Terminal interaction.
