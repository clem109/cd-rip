# Usage

## Output formats

Use `--format alac|aac|flac|wav` with `rip` or `watch`. ALAC is the default.
For AAC, choose `--aac-bitrate 128|192|256|320` (default 256).
FLAC never imports into Music. WAV carries ID3 tags, whose support varies by player.
Lossless formats undergo PCM identity checks; AAC uses decode and duration validation.
Different codecs and AAC bitrates use separate archive directories.

## Ripping performance

The drive reads one track at a time while a background worker encodes and verifies
the previous track. Artwork and lyrics are fetched concurrently. The queue is bounded
to one encoding track and one reading track; secure reading remains enabled.
The CD is ejected only after all audio has been verified and recorded in the archive.
Per-track `read_seconds` and `encode_verify_seconds` in `job.json` help identify bottlenecks
(these overlap, so they should not be added to estimate elapsed time).
Actual speed gains depend on the drive, disc, and output format; no hardware benchmark
has yet been run for the overlapping pipeline. Continuous batch reads and AccurateRip
verification are not implemented.

## Storage

The default archive is `~/Music/CD Rip/<disc-id>/`. Each folder contains ALAC tracks,
embedded tags, cover art, available lyrics, extraction logs, and `job.json` for resuming.

Set `CD_RIP_OUTPUT` or pass `--output` to use another archive. Pass the same output
directory to `status`. Set `CD_RIP_STATE_DIR` to override the process-lock directory,
normally `~/Library/Application Support/cd-rip/`.

The local `./cd-rip` development launcher preserves an existing `library/` directory.
The installed `cd-rip` command uses the default archive unless overridden.

## Identification and missing metadata

MusicBrainz identifies CDs by disc layout. Multiple editions prompt for a choice.
The Mac app recommends the most complete locally plausible match and selects it after a
10-second countdown. Choose another edition or pause the countdown when the packaging differs.
If the initial lookup fails, audio extraction continues and identification is retried
before tagging and Music import. If it still fails, the app shows Needs attention and
keeps the audio safely archived; Music import waits for identification.

```sh
cd-rip retry-metadata '/path/to/album'
cd-rip retry-metadata '/path/to/album' --release MUSICBRAINZ_RELEASE_UUID
cd-rip import-music '/path/to/album'
```

Use the UUID from a MusicBrainz **release** URL, not a release-group URL.
These commands do not need the disc. For manual artwork, put a JPEG named `cover.jpg`
in the album folder before retrying. Existing cached artwork and lyrics are preserved;
move them aside before switching to a different edition if they are incorrect.

Metadata comes from [MusicBrainz](https://musicbrainz.org/), artwork from the
[Cover Art Archive](https://coverartarchive.org/), and lyrics from [LRCLIB](https://lrclib.net/).
Lookups send disc IDs and track metadata to these services; audio stays local.
Plain lyrics are embedded; timed lyrics are saved as `.lrc` files.

## Music import

macOS may ask permission for Terminal to control Music. Music's copy-files setting
determines whether it copies the imported tracks or references the archive.
Later tag changes do not update separate copies already imported into Music.
No cloud upload or Sync Library settings are changed.

An interrupted import is marked uncertain to avoid duplicates. Check Music before retrying:

```sh
cd-rip import-music '/path/to/album' --retry-uncertain
```

Only use this flag if the uncertain track was not added. Completed imports are skipped.
Duplicate detection covers this tool's jobs, not previous Music imports.

## Read failures

If the drive reports busy when opening a track, the app checks that the same disc is
inserted, requests a normal unmount, and retries at most twice. It never force-unmounts
another app or disables secure reading. If the drive remains busy, stop CD playback or
importing in other apps, then choose **Resume Rip**.

Uncorrectable reads stop extraction and leave the disc inserted. Saved tracks and logs remain.
Use **Pause Rip** to stop the current read, then **Resume Rip** to retry the unfinished
track without rereading completed tracks. The CLI equivalent is `cd-rip rip`.
Reads stop if no audio is written for two minutes or output exceeds the expected track
length. Exact audio boundaries prevent enhanced CDs' extra sessions being read as music.
Failed partial WAVs are retained with a `.failed-…wav` name when retrying.
Clean/reinsert the disc if a read error persists. If a crash leaves an unrecorded
`.m4a`, move it aside before retrying; the tool refuses to overwrite it.

Use `--no-eject` to keep a completed CD inserted, or `--device /dev/diskN` with `rip`
to select a drive. Only mounted audio CDs are accepted.

Watch mode ignores a CD that was already inserted when the watcher started. In the Mac app,
choose **Scan for CD** to process the currently inserted disc without restarting the watcher.
For an incomplete album, **Resume Rip** replaces the scan control.
CD detection uses `diskutil` device metadata and does not inspect the mounted CD filesystem.

## Limits

The initial hardware test covered a standard nine-track audio CD on a USB optical drive,
including ALAC conversion, artwork, lyrics, eject, and Music import.
Mixed-mode discs, hidden tracks, pre-emphasis, unusual gaps, and drive-offset correction
are not validated. PCM equality confirms lossless encoding, not an independent reference rip.
