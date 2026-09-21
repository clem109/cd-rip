"""Offline tests: never access a real drive, Music, or metadata services."""

import argparse
import base64
import copy
import plistlib
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from mutagen.mp4 import MP4

from cdrip import cli as app

TOC = {
    "id": "test-disc",
    "first": 1,
    "last": 1,
    "leadout": 225,
    "tracks": [{"number": 1, "offset": 150, "sectors": 75}],
}
META = {
    "release_id": "11111111-1111-1111-1111-111111111111",
    "release_group": None,
    "album": "Test Album",
    "artist": "Test Artist",
    "date": "2026",
    "disc": 1,
    "disc_total": 1,
    "tracks": [{"title": "Test Track", "artist": "Test Artist"}],
}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def make_wav(path):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(b"".join(struct.pack("<hh", n % 1000, -(n % 1000)) for n in range(44100)))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_encode_is_lossless_and_tags_are_embedded(self):
        wav, target = self.folder / "test.wav", self.folder / "test.m4a"
        make_wav(wav)
        app.encode(wav, target)
        app.tag_file(target, META, 0, PNG, "Test lyric text")
        self.assertEqual(app.pcm_hash(wav), app.pcm_hash(target))
        tags = MP4(target)
        self.assertEqual(tags["\xa9lyr"], ["Test lyric text"])
        self.assertEqual(bytes(tags["covr"][0]), PNG)
        self.assertEqual(tags["trkn"], [(1, 1)])
        self.assertEqual(tags.info.codec, "alac")

    def test_disk_inventory_rejects_non_audio(self):
        data = {
            "AllDisksAndPartitions": [
                {"DeviceIdentifier": "disk0", "Content": "APFS"},
                {
                    "DeviceIdentifier": "disk5",
                    "Partitions": [{"DeviceIdentifier": "disk5s1", "Content": "CD_DA"}],
                },
            ]
        }
        with patch.object(app, "run", return_value=Mock(stdout=plistlib.dumps(data))):
            self.assertEqual(app.audio_devices(), {"/dev/disk5"})

    def test_wrong_release_layout_rejected(self):
        release = {
            "id": META["release_id"],
            "title": "Bad",
            "media": [{"discs": [{"id": TOC["id"]}], "tracks": [{"length": 120000}]}],
        }
        with self.assertRaises(ValueError):
            app.metadata_from_release(release, TOC)

    def test_import_success_skip_and_uncertain_failure(self):
        job = {"metadata": META, "files": [{"name": "test.m4a"}]}
        with patch.object(app, "run") as run:
            app.import_music(self.folder, job)
            app.import_music(self.folder, job)
            self.assertEqual(run.call_count, 1)
        job["files"] = [{"name": "other.m4a"}]
        with patch.object(app, "run", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                app.import_music(self.folder, job)
        with patch.object(app, "run") as run:
            with self.assertRaises(RuntimeError):
                app.import_music(self.folder, job)
            run.assert_not_called()

    def test_unknown_album_not_imported(self):
        with patch.object(app, "run") as run:
            app.import_music(self.folder, {"metadata": app.unknown(TOC), "files": []})
            run.assert_not_called()

    def test_watcher_ignores_current_then_processes_reinserted_cd(self):
        sequence = [{"/dev/disk5"}, {"/dev/disk5"}, set(), {"/dev/disk5"}]
        with (
            patch.object(app, "audio_devices", side_effect=sequence),
            patch.object(app, "process_disc", return_value=False) as process,
            patch.object(app.time, "sleep", side_effect=[None, None, KeyboardInterrupt]),
        ):
            with self.assertRaises(KeyboardInterrupt):
                app.watch(Mock())
        process.assert_called_once()

    def test_watcher_handles_next_disc_inserted_during_metadata(self):
        sequence = [set(), {"/dev/disk5"}, {"/dev/disk5"}]
        with (
            patch.object(app, "audio_devices", side_effect=sequence),
            patch.object(app, "process_disc", return_value=True) as process,
            patch.object(app.time, "sleep", side_effect=[None, KeyboardInterrupt]),
        ):
            with self.assertRaises(KeyboardInterrupt):
                app.watch(Mock())
        self.assertEqual(process.call_count, 2)

    def test_full_pipeline_with_fake_drive_and_network(self):
        real_run = app.run
        events = []

        def fake_run(args, **kwargs):
            if args[0] == "cd-paranoia":
                events.append("extract")
                make_wav(args[-1])
                return Mock(returncode=0)
            if args[0] == "diskutil":
                events.append(args[1])
                return Mock(returncode=0)
            if args[0] == "osascript":
                events.append("music")
                return Mock(returncode=0)
            return real_run(args, **kwargs)

        def response(url, binary=False):
            return (
                PNG
                if binary
                else {"plainLyrics": "Test lyrics", "syncedLyrics": "[00:00.00]Test lyrics"}
            )

        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=False
        )
        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=copy.deepcopy(TOC)),
            patch.object(app, "identify", return_value=copy.deepcopy(META)),
            patch.object(app.Client, "get", side_effect=response),
            patch.object(app, "run", side_effect=fake_run),
        ):
            self.assertTrue(app.process_disc("/dev/disk5", args))
            app.process_disc("/dev/disk5", args)  # completed disc must not rip/import again
        self.assertEqual(events, ["unmountDisk", "extract", "eject", "music"])
        job = app.load_job(self.folder / TOC["id"])
        self.assertTrue(job["audio_complete"])
        self.assertTrue(job["files"][0]["imported"])
        self.assertEqual(job["warnings"], [])
        self.assertTrue((self.folder / TOC["id"] / "01.lrc").exists())

    def test_read_failure_never_ejects_or_imports(self):
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=False
        )

        def fail(args, **kwargs):
            if args[0] == "cd-paranoia":
                raise subprocess.CalledProcessError(1, args)
            return Mock()

        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=TOC),
            patch.object(app, "identify", return_value=META),
            patch.object(app, "run", side_effect=fail) as run,
            patch.object(app.subprocess, "run") as mount,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                app.process_disc("/dev/disk5", args)
        self.assertFalse(
            any(c.args[0][0] == "osascript" or "eject" in c.args[0] for c in run.call_args_list)
        )
        self.assertEqual(mount.call_args.args[0], ["diskutil", "mountDisk", "/dev/disk5"])

    def test_path_escape_rejected(self):
        app.save(self.folder / "job.json", {"files": [{"name": "../outside.m4a"}]})
        with self.assertRaises(ValueError):
            app.load_job(self.folder)


if __name__ == "__main__":
    unittest.main()
