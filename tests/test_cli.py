"""Offline tests: never access a real drive, Music, or metadata services."""

import argparse
import base64
import copy
import io
import plistlib
import struct
import subprocess
import tempfile
import threading
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

    def test_disk_inventory_detects_audio_from_device_metadata(self):
        inventory = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": "disk5",
                    "Content": "CD_partition_scheme",
                    "MountPoint": "/Volumes/Test CD",
                }
            ]
        }
        info = {"FilesystemName": "CD-DA", "FilesystemUserVisibleName": "CD Audio"}

        def run(command, **kwargs):
            self.assertNotIn(".TOC.plist", " ".join(command))
            payload = inventory if command[1] == "list" else info
            return Mock(stdout=plistlib.dumps(payload))

        with patch.object(app, "run", side_effect=run) as run_mock:
            self.assertEqual(app.audio_devices(), {"/dev/disk5"})
        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(
            run_mock.call_args_list[1].args[0], ["diskutil", "info", "-plist", "/dev/disk5"]
        )

    def test_edition_ranking_prefers_local_then_regional_complete_match(self):
        australia = {"country": "AU", "date": "2008", "label": "Fiction", "catalogue": "1"}
        europe = {"country": "XE", "date": "2008-03-17", "label": "Fiction", "catalogue": "1"}
        british = {"country": "GB", "date": "2008", "label": "Fiction", "catalogue": "1"}
        self.assertIs(app.rank_editions([australia, europe], "GB")[0], europe)
        self.assertIs(app.rank_editions([europe, british], "GB")[0], british)

    def test_all_formats_encode_and_embed_metadata(self):
        from mutagen.flac import FLAC
        from mutagen.wave import WAVE

        wav = self.folder / "source.wav"
        make_wav(wav)
        for fmt, (extension, _) in app.FORMATS.items():
            with self.subTest(format=fmt):
                target = self.folder / f"result-{fmt}.{extension}"
                app.encode(wav, target, fmt, 256)
                app.tag_file(target, META, 0, PNG, "Test lyrics")
                if fmt != "aac":
                    self.assertEqual(app.pcm_hash(wav), app.pcm_hash(target))
                if fmt == "flac":
                    audio = FLAC(target)
                    self.assertEqual(audio["lyrics"], ["Test lyrics"])
                    self.assertEqual(audio.pictures[0].data, PNG)
                elif fmt == "wav":
                    audio = WAVE(target)
                    self.assertEqual(audio.tags.getall("USLT")[0].text, "Test lyrics")
                    self.assertEqual(audio.tags.getall("APIC")[0].data, PNG)
                else:
                    audio = MP4(target)
                    self.assertEqual(audio["\xa9lyr"], ["Test lyrics"])
                    self.assertEqual(bytes(audio["covr"][0]), PNG)

    def test_flac_cannot_be_imported_to_music(self):
        job = {"format": "flac", "metadata": META, "files": [{"name": "test.flac"}]}
        with patch.object(app, "run") as run:
            with self.assertRaisesRegex(ValueError, "FLAC"):
                app.import_music(self.folder, job)
        run.assert_not_called()

    def test_wrong_release_layout_rejected(self):
        release = {
            "id": META["release_id"],
            "title": "Bad",
            "media": [{"discs": [{"id": TOC["id"]}], "tracks": [{"length": 120000}]}],
        }
        with self.assertRaises(ValueError):
            app.metadata_from_release(release, TOC)

    def test_edition_details_are_preserved_for_picker(self):
        release = {
            "id": META["release_id"],
            "title": "Test Album",
            "country": "GB",
            "date": "1995-10-02",
            "disambiguation": "original pressing",
            "label-info": [{"label": {"name": "Creation"}, "catalog-number": "CRE CD 189"}],
            "media": [
                {"discs": [{"id": TOC["id"]}], "tracks": [{"title": "Test Track", "length": 1000}]}
            ],
        }
        metadata = app.metadata_from_release(release, TOC)
        self.assertEqual(metadata["country"], "GB")
        self.assertEqual(metadata["label"], "Creation")
        self.assertEqual(metadata["catalogue"], "CRE CD 189")
        self.assertEqual(metadata["disambiguation"], "original pressing")

    def test_null_label_fields_do_not_discard_valid_disc_matches(self):
        release = {
            "id": META["release_id"],
            "title": "Corinne Bailey Rae",
            "label-info": [
                {"label": {"name": "EMI"}, "catalog-number": "009463 56544 2 4"},
                {"label": {"name": "GoodGroove"}, "catalog-number": None},
                {"label": None, "catalog-number": None},
            ],
            "media": [
                {"discs": [{"id": TOC["id"]}], "tracks": [{"title": "Test", "length": 1000}]}
            ],
        }
        client = Mock()
        client.get.return_value = {"releases": [release]}
        metadata = app.identify(client, TOC)
        self.assertEqual(metadata["label"], "EMI, GoodGroove")
        self.assertEqual(metadata["catalogue"], "009463 56544 2 4")
        release["label-info"] = None
        self.assertEqual(app.identify(client, TOC)["catalogue"], "")

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

    def test_gui_import_uses_native_app_and_requires_confirmation(self):
        import queue

        results = queue.Queue()
        path = str((self.folder / "test.m4a").resolve())
        results.put({"path": path, "ok": True})
        job = {"metadata": META, "files": [{"name": "test.m4a"}]}
        with (
            patch.object(app, "GUI", True),
            patch.object(app, "IMPORT_RESULTS", results),
            patch.object(app, "event") as event,
            patch.object(app, "run") as run,
        ):
            app.import_music(self.folder, job)
        run.assert_not_called()
        self.assertTrue(job["files"][0]["imported"])
        self.assertTrue(any(c.args[0] == "import_request" for c in event.call_args_list))

    def test_gui_import_failure_keeps_uncertain_track_pending(self):
        import queue

        results = queue.Queue()
        path = str((self.folder / "test.m4a").resolve())
        results.put({"path": path, "ok": False, "error": "Automation permission denied"})
        job = {"metadata": META, "files": [{"name": "test.m4a"}]}
        with (
            patch.object(app, "GUI", True),
            patch.object(app, "IMPORT_RESULTS", results),
            patch.object(app, "event"),
        ):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                app.import_music(self.folder, job)
        self.assertTrue(job["files"][0]["import_pending"])
        self.assertFalse(job["files"][0].get("imported"))

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
        fetching = threading.Event()
        extracting = threading.Event()

        def fake_run(args, **kwargs):
            if args[0] == "cd-paranoia":
                extracting.set()
                self.assertTrue(fetching.wait(3), "Metadata should start during extraction")
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
            fetching.set()
            self.assertTrue(extracting.wait(3), "Extraction must not wait for metadata")
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
        tags = MP4(self.folder / TOC["id"] / job["files"][0]["name"])
        self.assertEqual(tags["\xa9lyr"], ["Test lyrics"])
        self.assertEqual(bytes(tags["covr"][0]), PNG)
        self.assertEqual(
            app.digest(self.folder / TOC["id"] / job["files"][0]["name"]), job["files"][0]["sha256"]
        )

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
            patch.object(app.Client, "get", return_value=None),
            patch.object(app, "run", side_effect=fail) as run,
            patch.object(app.subprocess, "run") as mount,
        ):
            with self.assertRaisesRegex(RuntimeError, "could not be read securely"):
                app.process_disc("/dev/disk5", args)
        self.assertFalse(
            any(c.args[0][0] == "osascript" or "eject" in c.args[0] for c in run.call_args_list)
        )
        self.assertEqual(mount.call_args.args[0], ["diskutil", "mountDisk", "/dev/disk5"])

    def test_transient_identification_failure_is_retried_before_import(self):
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=True
        )
        real_run = app.run

        def run(command, **kwargs):
            if command[0] == "cd-paranoia":
                make_wav(command[-1])
                return Mock()
            if command[0] in ("diskutil", "osascript"):
                return Mock()
            return real_run(command, **kwargs)

        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=copy.deepcopy(TOC)),
            patch.object(
                app, "identify", side_effect=[TimeoutError("offline"), copy.deepcopy(META)]
            ) as identify,
            patch.object(app, "fetch_enrichment", return_value=(None, [None], [])),
            patch.object(app, "run", side_effect=run),
        ):
            app.process_disc("/dev/disk5", args)

        job = app.load_job(self.folder / TOC["id"])
        self.assertEqual(identify.call_count, 2)
        self.assertEqual(job["metadata"]["album"], META["album"])
        self.assertTrue(job["files"][0]["imported"])
        tags = MP4(self.folder / TOC["id"] / job["files"][0]["name"])
        self.assertEqual(tags["\xa9alb"], [META["album"]])

    def test_unidentified_after_retry_preserves_audio_and_reports_attention(self):
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=True
        )
        real_run = app.run

        def run(command, **kwargs):
            if command[0] == "cd-paranoia":
                make_wav(command[-1])
                return Mock()
            if command[0] == "diskutil":
                return Mock()
            return real_run(command, **kwargs)

        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=copy.deepcopy(TOC)),
            patch.object(app, "identify", return_value=None) as identify,
            patch.object(app, "fetch_enrichment", return_value=(None, [None], [])),
            patch.object(app, "run", side_effect=run),
            patch.object(app, "import_music") as import_music,
            patch.object(app, "event") as event,
        ):
            app.process_disc("/dev/disk5", args)

        job = app.load_job(self.folder / TOC["id"])
        self.assertEqual(identify.call_count, 2)
        self.assertTrue(job["audio_complete"])
        self.assertEqual(len(job["files"]), 1)
        self.assertIn("unidentified after retrying", job["postprocess_error"])
        import_music.assert_not_called()
        completion = next(c.kwargs for c in event.call_args_list if c.args[0] == "complete")
        self.assertEqual(completion["error"], job["postprocess_error"])

    def run_overlapping_pipeline(self, failure=None, resume=False):
        toc = copy.deepcopy(TOC)
        toc.update(last=2, leadout=300)
        toc["tracks"].append({"number": 2, "offset": 225, "sectors": 75})
        meta = copy.deepcopy(META)
        meta["tracks"].append({"title": "Second", "artist": "Test Artist"})
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=True
        )
        encoding, second_read = threading.Event(), threading.Event()
        if resume:
            encoding.set()  # First track is already committed; only track two is read.
        real_prepare = app.prepare_track
        real_run = app.run
        native_run = subprocess.run
        operations = []

        def prepare(*values):
            if values[3] == 0:
                encoding.set()
                self.assertTrue(second_read.wait(5), "Next read must overlap encoding")
            if failure == "encode":
                raise RuntimeError("encoding failed")
            return real_prepare(*values)

        def run(command, **kwargs):
            if command[0] == "cd-paranoia":
                operations.append("read")
                if command[-2].startswith("2["):
                    self.assertTrue(encoding.wait(5))
                    second_read.set()
                    if failure == "read":
                        raise RuntimeError("reading failed")
                make_wav(command[-1])
                return Mock()
            if command[0] in ("diskutil", "osascript"):
                operations.append(command[1] if command[0] == "diskutil" else "music")
                if "eject" in command:
                    job = app.load_job(self.folder / TOC["id"])
                    self.assertTrue(job["audio_complete"])
                    self.assertEqual(len(job["files"]), 2)
                return Mock()
            return real_run(command, **kwargs)

        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=toc),
            patch.object(app, "identify", return_value=meta),
            patch.object(app, "fetch_enrichment", return_value=(None, [None, None], [])),
            patch.object(app, "prepare_track", side_effect=prepare),
            patch.object(app, "run", side_effect=run),
            patch.object(app.subprocess, "run") as subprocess_run,
        ):
            # Remounts must be mocked separately from real ffmpeg subprocesses.
            subprocess_run.side_effect = lambda cmd, **kw: (
                Mock() if cmd[0] == "diskutil" else native_run(cmd, **kw)
            )
            if failure:
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    app.process_disc("/dev/disk5", args)
            else:
                app.process_disc("/dev/disk5", args)
        return app.load_job(self.folder / TOC["id"]), operations

    def test_encoding_overlaps_next_read_and_commits_in_order(self):
        job, operations = self.run_overlapping_pipeline()
        self.assertEqual([entry["name"][:2] for entry in job["files"]], ["01", "02"])
        self.assertEqual(operations, ["unmountDisk", "read", "read", "eject", "music", "music"])
        for entry in job["files"]:
            self.assertGreaterEqual(entry["read_seconds"], 0)
            self.assertGreater(entry["encode_verify_seconds"], 0)
        self.assertFalse(list((self.folder / TOC["id"]).glob("*.partial.wav")))

    def test_later_read_failure_preserves_previous_encoded_track(self):
        job, operations = self.run_overlapping_pipeline("read")
        self.assertFalse(job["audio_complete"])
        self.assertEqual(len(job["files"]), 1)
        self.assertNotIn("eject", operations)
        self.assertNotIn("music", operations)

    def test_encoder_failure_retains_pcm_and_never_ejects(self):
        job, operations = self.run_overlapping_pipeline("encode")
        self.assertFalse(job["audio_complete"])
        self.assertEqual(job["files"], [])
        self.assertEqual(len(list((self.folder / TOC["id"]).glob("*.partial.wav"))), 2)
        self.assertNotIn("eject", operations)
        self.assertNotIn("music", operations)

    def test_pipeline_resumes_after_read_failure_without_rereading_saved_track(self):
        first, _ = self.run_overlapping_pipeline("read")
        resumed, operations = self.run_overlapping_pipeline(resume=True)
        self.assertEqual(operations.count("read"), 1)
        self.assertTrue(resumed["audio_complete"])
        self.assertEqual(first["files"][0]["sha256"], resumed["files"][0]["sha256"])

    def test_worker_tagging_failure_does_not_publish_or_remove_pcm(self):
        wav, target = self.folder / "source.wav", self.folder / "result.m4a"
        make_wav(wav)
        with patch.object(app, "tag_file", side_effect=RuntimeError("tag failed")):
            with self.assertRaisesRegex(RuntimeError, "tag failed"):
                app.prepare_track(wav, target, META, 0, "alac", 256)
        self.assertTrue(wav.exists())
        self.assertFalse(target.exists())
        self.assertFalse((self.folder / "job.json").exists())

    def test_busy_drive_retries_only_open_failures(self):
        busy = b"Resource busy\nUnable to open cdrom drive\n"
        for mode in ("recovered", "busy", "read_error", "changed"):
            with self.subTest(mode=mode):
                calls = []
                log = self.folder / f"{mode}.log"
                # Old busy errors must not cause retry of a new audio error.
                log.write_bytes(busy)

                def run(command, **kwargs):
                    calls.append(command)
                    if command[0] == "cd-paranoia":
                        reads = sum(c[0] == "cd-paranoia" for c in calls)
                        if mode == "recovered" and reads == 2:
                            return Mock()
                        kwargs["stderr"].write(
                            b"unrecoverable skip\n" if mode == "read_error" else busy
                        )
                        kwargs["stderr"].flush()
                        raise subprocess.CalledProcessError(1, command)
                    return Mock()

                with (
                    patch.object(app, "run", side_effect=run),
                    patch.object(app, "read_toc", return_value={} if mode == "changed" else TOC),
                    patch.object(app.time, "sleep"),
                ):
                    if mode == "recovered":
                        app.extract_track("/dev/disk5", TOC, 1, self.folder / "x.wav", log)
                    else:
                        with self.assertRaises(RuntimeError):
                            app.extract_track("/dev/disk5", TOC, 1, self.folder / "x.wav", log)
                self.assertEqual(
                    sum(c[0] == "cd-paranoia" for c in calls),
                    {"recovered": 2, "busy": 3, "read_error": 1, "changed": 1}[mode],
                )
                self.assertFalse(any("force" in c for c in calls))

    def test_extraction_bounds_final_track_and_preserves_failed_pcm(self):
        toc = {"tracks": [{"number": 10, "sectors": 30259}]}
        wav = self.folder / "10.partial.wav"
        wav.write_bytes(b"previous failed read")
        with patch.object(app, "run") as run:
            app.extract_track("/dev/disk5", toc, 10, wav, self.folder / "10.rip.log")
        command = run.call_args.args[0]
        self.assertEqual(command[-2], "10[.0]-10[.30258]")
        self.assertIn("-X", command)
        self.assertEqual(run.call_args.kwargs["expected_bytes"], 30259 * 2352)
        self.assertEqual(
            next(self.folder.glob("10.failed-*.wav")).read_bytes(), b"previous failed read"
        )

    def test_reader_watchdog_stops_overrun_stall_and_user_pause(self):
        for mode in ("overrun", "stall", "pause", "timeout"):
            with self.subTest(mode=mode):
                wav = self.folder / "read.wav"
                wav.write_bytes(b"x" * (200 if mode == "overrun" else 44))
                reader = Mock()
                reader.poll.return_value = None
                context = Mock()
                context.__enter__ = Mock(return_value=reader)
                context.__exit__ = Mock(return_value=False)
                pause = threading.Event()
                if mode == "pause":
                    pause.set()
                times = [0, 0, 121] if mode == "stall" else [0, 2]
                with (
                    patch.object(app.subprocess, "Popen", return_value=context),
                    patch.object(app.time, "monotonic", side_effect=times),
                    patch.object(app.time, "sleep"),
                    patch.object(app, "PAUSE_READ", pause),
                ):
                    with self.assertRaises(RuntimeError):
                        app.monitored_read(
                            ["reader"], wav, 100, timeout=1 if mode == "timeout" else 1800
                        )
                reader.terminate.assert_called_once()
                reader.wait.assert_called_once_with(timeout=5)
                self.assertTrue(wav.exists())

    def test_prefetch_does_not_mutate_manifest_or_require_audio(self):
        job = {"metadata": copy.deepcopy(META), "toc": copy.deepcopy(TOC), "files": []}
        before = copy.deepcopy(job)
        client = Mock()
        client.get.return_value = PNG
        cover, lyrics, warnings = app.fetch_enrichment(client, self.folder, job, False)
        self.assertEqual(cover, PNG)
        self.assertEqual(lyrics, [None])
        self.assertEqual(warnings, [])
        self.assertEqual(job, before)
        self.assertFalse((self.folder / "job.json").exists())
        client.get.assert_called_once()

    def test_postprocessing_error_is_saved_and_included_in_completion(self):
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=False
        )
        real_run = app.run

        def fake_run(command, **kwargs):
            if command[0] == "cd-paranoia":
                make_wav(command[-1])
                return Mock()
            if command[0] == "diskutil":
                return Mock()
            return real_run(command, **kwargs)

        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=TOC),
            patch.object(app, "identify", return_value=META),
            patch.object(app, "fetch_enrichment", return_value=(None, [None], [])),
            patch.object(app, "run", side_effect=fake_run),
            patch.object(app, "import_music", side_effect=RuntimeError("Music unavailable")),
            patch.object(app, "event") as event,
        ):
            app.process_disc("/dev/disk5", args)
        job = app.load_job(self.folder / TOC["id"])
        self.assertEqual(job["postprocess_error"], "Music unavailable")
        completion = next(c for c in event.call_args_list if c.args[0] == "complete")
        self.assertEqual(completion.kwargs["error"], "Music unavailable")

    def test_prefetch_network_failure_returns_notices(self):
        job = {"metadata": META, "toc": TOC, "files": []}
        client = Mock()
        client.get.side_effect = TimeoutError("offline")
        cover, lyrics, warnings = app.fetch_enrichment(client, self.folder, job)
        self.assertIsNone(cover)
        self.assertEqual(lyrics, [None])
        self.assertTrue(any("Artwork" in warning for warning in warnings))
        self.assertTrue(any("Lyrics" in warning for warning in warnings))

    def test_path_escape_rejected(self):
        app.save(self.folder / "job.json", {"files": [{"name": "../outside.m4a"}]})
        with self.assertRaises(ValueError):
            app.load_job(self.folder)

    def test_gui_controls_select_and_request_graceful_stop(self):
        import queue
        import threading

        stop, scan, choices = threading.Event(), threading.Event(), queue.Queue()
        stream = io.StringIO(
            'invalid\n[]\n{"command":"choose","choice":2}\n{"command":"scan"}\n{"command":"stop"}\n'
        )
        with (
            patch.object(app.sys, "stdin", stream),
            patch.object(app, "STOP", stop),
            patch.object(app, "SCAN", scan),
            patch.object(app, "CHOICES", choices),
        ):
            app.read_controls()
        self.assertEqual(choices.get_nowait(), "2")
        self.assertTrue(scan.is_set())
        self.assertTrue(stop.is_set())

    def test_scan_processes_disc_that_was_present_when_watcher_started(self):
        scan = threading.Event()
        scan.set()
        with (
            patch.object(app, "SCAN", scan),
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "process_disc", return_value=False) as process,
            patch.object(app.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                app.watch(Mock())
        process.assert_called_once()

    def test_gui_messages_are_machine_readable(self):
        import json

        output = io.StringIO()
        with patch.object(app, "GUI", True), patch.object(app.sys, "stdout", output):
            app.say('Album "test"\nnext line')
        self.assertEqual(
            json.loads(output.getvalue()), {"event": "log", "message": 'Album "test"\nnext line'}
        )

    def test_gui_edition_choice_works_without_terminal(self):
        import queue

        choices = queue.Queue()
        choices.put("2")
        client = Mock()
        client.get.return_value = {"releases": [{"id": "one"}, {"id": "two"}]}
        first, second = dict(META, date="1986"), dict(META, date="1993")
        with (
            patch.object(app, "GUI", True),
            patch.object(app, "CHOICES", choices),
            patch.object(app, "metadata_from_release", side_effect=[first, second]),
            patch.object(app, "event") as event,
        ):
            self.assertEqual(app.identify(client, TOC), second)
        self.assertTrue(any(call.args[0] == "choices" for call in event.call_args_list))

    def test_watcher_stops_after_current_job(self):
        import threading

        stop = threading.Event()

        def complete_current(*args):
            stop.set()
            return True

        with (
            patch.object(app, "STOP", stop),
            patch.object(app, "audio_devices", side_effect=[set(), {"/dev/disk5", "/dev/disk6"}]),
            patch.object(app, "process_disc", side_effect=complete_current) as process,
            patch.object(app.time, "sleep"),
        ):
            app.watch(Mock())
        process.assert_called_once()

    def test_stop_before_rip_leaves_drive_alone(self):
        import threading

        stop = threading.Event()
        stop.set()
        args = argparse.Namespace(
            output=self.folder, release=None, no_music=False, no_eject=False, no_lyrics=False
        )
        with (
            patch.object(app, "audio_devices", return_value={"/dev/disk5"}),
            patch.object(app, "read_toc", return_value=TOC),
            patch.object(app, "identify", return_value=META),
            patch.object(app, "STOP", stop),
            patch.object(app, "run") as run,
        ):
            self.assertFalse(app.process_disc("/dev/disk5", args))
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
