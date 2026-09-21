#!/usr/bin/env python3
"""Local macOS CD -> ALAC pipeline. No hardware access until watch/rip is invoked."""

import argparse
import copy
import ctypes as C
import ctypes.util
import fcntl
import hashlib
import json
import os
import plistlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__

DEFAULT_OUTPUT = Path(os.environ.get("CD_RIP_OUTPUT", str(Path.home() / "Music" / "CD Rip")))
STATE_DIR = Path(
    os.environ.get(
        "CD_RIP_STATE_DIR", str(Path.home() / "Library" / "Application Support" / "cd-rip")
    )
)
MB = "https://musicbrainz.org/ws/2"
UA = f"cd-rip/{__version__} (https://github.com/clem109/cd-rip)"
GUI = os.environ.get("CD_RIP_GUI") == "1"
STOP = threading.Event()
CHOICES = queue.Queue()
IMPORT_RESULTS = queue.Queue()
OUTPUT_LOCK = threading.Lock()


def event(kind, **values):
    if GUI:
        with OUTPUT_LOCK:
            print(json.dumps({"event": kind, **values}), flush=True)


def read_controls():
    """The app sends newline-delimited JSON on stdin; no shell commands."""
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                continue
            if message.get("command") == "stop":
                STOP.set()
                CHOICES.put("")
            elif message.get("command") == "choose":
                CHOICES.put(str(message.get("choice", "")))
            elif message.get("command") == "import_result":
                IMPORT_RESULTS.put(message)
        except (ValueError, TypeError):
            continue
    # If the app exits unexpectedly, finish the current disc, then stop.
    STOP.set()
    CHOICES.put("")


def say(message):
    if GUI:
        event("log", message=message)
    else:
        print(message, flush=True)


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def safe(text):
    text = re.sub(r"[\x00-\x1f/\\:]", "_", text).strip(" .")
    return text[:100] or "Unknown"


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def libdiscid():
    candidates = [
        str(Path(getattr(sys, "_MEIPASS", "/nonexistent")) / "libdiscid.dylib"),
        "/opt/homebrew/lib/libdiscid.dylib",
        "/usr/local/lib/libdiscid.dylib",
        ctypes.util.find_library("discid"),
    ]
    for name in candidates:
        if name:
            try:
                return C.CDLL(name)
            except OSError:
                pass
    raise RuntimeError("libdiscid missing: brew install libdiscid libcdio-paranoia")


def read_toc(device):
    if sys.platform == "darwin" and re.fullmatch(r"/dev/disk\d+", device):
        device = device.replace("/dev/disk", "/dev/rdisk", 1)
    lib = libdiscid()
    signatures = {
        "discid_new": (C.c_void_p, []),
        "discid_free": (None, [C.c_void_p]),
        "discid_read_sparse": (C.c_int, [C.c_void_p, C.c_char_p, C.c_uint]),
        "discid_get_id": (C.c_char_p, [C.c_void_p]),
        "discid_get_error_msg": (C.c_char_p, [C.c_void_p]),
        "discid_get_first_track_num": (C.c_int, [C.c_void_p]),
        "discid_get_last_track_num": (C.c_int, [C.c_void_p]),
        "discid_get_sectors": (C.c_int, [C.c_void_p]),
        "discid_get_track_offset": (C.c_int, [C.c_void_p, C.c_int]),
        "discid_get_track_length": (C.c_int, [C.c_void_p, C.c_int]),
    }
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, argtypes
    disc = lib.discid_new()
    if not disc:
        raise RuntimeError("Could not allocate disc reader")
    try:
        if not lib.discid_read_sparse(disc, device.encode(), 0):
            raise RuntimeError(lib.discid_get_error_msg(disc).decode())
        first = lib.discid_get_first_track_num(disc)
        last = lib.discid_get_last_track_num(disc)
        return {
            "id": lib.discid_get_id(disc).decode(),
            "first": first,
            "last": last,
            "leadout": lib.discid_get_sectors(disc),
            "tracks": [
                {
                    "number": n,
                    "offset": lib.discid_get_track_offset(disc, n),
                    "sectors": lib.discid_get_track_length(disc, n),
                }
                for n in range(first, last + 1)
            ],
        }
    finally:
        lib.discid_free(disc)


def audio_devices():
    """Read the OS disk inventory, not audio sectors. Only mounted audio CDs."""
    result = run(["diskutil", "list", "-plist"], capture_output=True, timeout=15)
    tree = plistlib.loads(result.stdout)
    found = set()

    def walk(node, parent=None):
        if isinstance(node, dict):
            device = node.get("DeviceIdentifier", parent)
            mount = node.get("MountPoint")
            if node.get("Content") == "CD_DA" or (mount and (Path(mount) / ".TOC.plist").is_file()):
                if device and re.fullmatch(r"disk\d+(s\d+)*", device):
                    found.add("/dev/" + re.match(r"disk\d+", device)[0])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, device)
        elif isinstance(node, list):
            for value in node:
                walk(value, parent)

    walk(tree)
    return found


class Client:
    def __init__(self):
        self.last_request = 0

    def get(self, url, binary=False):
        # Conservative pacing for all metadata providers; bounded retries.
        for attempt in range(3):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as response:
                    data = response.read(12_000_001)
                if len(data) > 12_000_000:
                    raise ValueError("Provider response exceeds size limit")
                return data if binary else json.loads(data)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2**attempt)


def artist(credits):
    return (
        "".join(
            c.get("name", c.get("artist", {}).get("name", "")) + c.get("joinphrase", "")
            for c in credits or []
        )
        or "Unknown Artist"
    )


def metadata_from_release(release, toc, require_id=True):
    for medium in release.get("media", []):
        if require_id and toc["id"] not in [d["id"] for d in medium.get("discs", [])]:
            continue
        tracks = medium.get("tracks", [])
        if len(tracks) != len(toc["tracks"]):
            continue
        # Reject a differently ordered/different-duration edition.
        if any(
            t.get("length") and abs(t["length"] / 1000 - d["sectors"] / 75) > 3
            for t, d in zip(tracks, toc["tracks"])
        ):
            continue
        return {
            "release_id": release["id"],
            "release_group": release.get("release-group", {}).get("id"),
            "album": release["title"],
            "artist": artist(release.get("artist-credit")),
            "date": release.get("date", ""),
            "country": release.get("country", ""),
            "disambiguation": release.get("disambiguation", ""),
            "label": ", ".join(
                info.get("label", {}).get("name", "") for info in release.get("label-info", [])
            ),
            "catalogue": ", ".join(
                info.get("catalog-number", "") for info in release.get("label-info", [])
            ),
            "disc": medium.get("position", 1),
            "disc_total": len(release["media"]),
            "tracks": [
                {
                    "title": t.get("title") or t["recording"]["title"],
                    "artist": artist(
                        t.get("artist-credit")
                        or t.get("recording", {}).get("artist-credit")
                        or release.get("artist-credit")
                    ),
                }
                for t in tracks
            ],
        }
    raise ValueError("Release does not match this disc's track layout/durations")


def identify(client, toc, release_id=None):
    if release_id:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", release_id):
            raise ValueError("Use a MusicBrainz release UUID, not a URL")
        result = client.get(
            f"{MB}/release/{release_id}?inc=recordings+artist-credits+discids+release-groups+labels&fmt=json"
        )
        if not result:
            raise ValueError("Release not found")
        return metadata_from_release(result, toc, require_id=False)
    result = client.get(
        f"{MB}/discid/{toc['id']}?inc=recordings+artist-credits+release-groups+labels&fmt=json"
    )
    candidates = []
    for release in (result or {}).get("releases", []):
        try:
            candidates.append(metadata_from_release(release, toc))
        except (ValueError, KeyError):
            pass
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    say("Multiple matching editions:")
    for i, m in enumerate(candidates, 1):
        say(f"  {i}. {m['artist']} — {m['album']} ({m['date']}) [{m['release_id']}]")
    if GUI:
        event("choices", releases=candidates)
        choice = CHOICES.get()
    elif not sys.stdin.isatty():
        say("No interactive terminal: ripping with placeholder tags; resolve later with --release.")
        return None
    else:
        choice = input("Edition number (Enter = identify later): ").strip()
    if not choice:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
        raise ValueError("Invalid edition choice")
    return candidates[int(choice) - 1]


def unknown(toc):
    return {
        "album": "Unknown CD " + toc["id"][:8],
        "artist": "Unknown Artist",
        "date": "",
        "disc": 1,
        "disc_total": 1,
        "tracks": [
            {"title": f"Track {t['number']:02}", "artist": "Unknown Artist"} for t in toc["tracks"]
        ],
    }


def pcm_hash(path):
    return run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        capture_output=True,
        timeout=600,
    ).stdout


FORMATS = {
    "alac": ("m4a", "alac"),
    "aac": ("m4a", "aac"),
    "flac": ("flac", "flac"),
    "wav": ("wav", "pcm_s16le"),
}


def encode(wav, target, audio_format="alac", bitrate=256):
    extension, codec = FORMATS[audio_format]
    temp = target.with_suffix(".encoding." + extension)
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            wav,
            "-map",
            "0:a:0",
            "-c:a",
            codec,
            *(["-b:a", f"{bitrate}k"] if audio_format == "aac" else []),
            temp,
        ],
        timeout=600,
    )
    if audio_format == "aac":
        import wave

        with wave.open(str(wav)) as source:
            duration = source.getnframes() / source.getframerate()
        info = json.loads(
            run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", temp],
                capture_output=True,
                timeout=60,
            ).stdout
        )["streams"][0]
        if (
            int(info["channels"]) != 2
            or int(info["sample_rate"]) != 44100
            or abs(float(info["duration"]) - duration) > 0.1
        ):
            raise RuntimeError("AAC duration or audio format differs; original WAV retained")
        run(["ffmpeg", "-v", "error", "-xerror", "-i", temp, "-f", "null", "-"], timeout=600)
    elif pcm_hash(wav) != pcm_hash(temp):
        raise RuntimeError("Lossless audio does not match extracted PCM; original WAV retained")
    temp.replace(target)


def tag_file(path, metadata, index, cover=None, lyrics=None):
    if path.suffix.lower() in (".flac", ".wav"):
        return tag_other_format(path, metadata, index, cover, lyrics)
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    track = metadata["tracks"][index]
    audio["\xa9nam"] = [track["title"]]
    audio["\xa9ART"] = [track["artist"]]
    audio["aART"] = [metadata["artist"]]
    audio["\xa9alb"] = [metadata["album"]]
    audio["trkn"] = [(index + 1, len(metadata["tracks"]))]
    audio["disk"] = [(metadata["disc"], metadata["disc_total"])]
    if metadata.get("date"):
        audio["\xa9day"] = [metadata["date"]]
    if cover:
        fmt = (
            MP4Cover.FORMAT_PNG if cover.startswith(b"\x89PNG\r\n\x1a\n") else MP4Cover.FORMAT_JPEG
        )
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    if lyrics:
        audio["\xa9lyr"] = [lyrics]
    audio.save()


def tag_other_format(path, metadata, index, cover=None, lyrics=None):
    track = metadata["tracks"][index]
    mime = "image/png" if cover and cover.startswith(b"\x89PNG") else "image/jpeg"
    if path.suffix.lower() == ".flac":
        from mutagen.flac import FLAC, Picture

        audio = FLAC(path)
        audio.update(
            {
                "title": track["title"],
                "artist": track["artist"],
                "albumartist": metadata["artist"],
                "album": metadata["album"],
                "tracknumber": str(index + 1),
                "tracktotal": str(len(metadata["tracks"])),
                "discnumber": str(metadata["disc"]),
                "disctotal": str(metadata["disc_total"]),
                "date": metadata.get("date", ""),
            }
        )
        if lyrics:
            audio["lyrics"] = lyrics
        if cover:
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, mime, cover
            audio.clear_pictures()
            audio.add_picture(picture)
    else:
        from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT
        from mutagen.wave import WAVE

        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        for frame in [
            TIT2(encoding=3, text=track["title"]),
            TPE1(encoding=3, text=track["artist"]),
            TPE2(encoding=3, text=metadata["artist"]),
            TALB(encoding=3, text=metadata["album"]),
            TRCK(encoding=3, text=f"{index + 1}/{len(metadata['tracks'])}"),
            TPOS(encoding=3, text=f"{metadata['disc']}/{metadata['disc_total']}"),
            TDRC(encoding=3, text=metadata.get("date", "")),
        ]:
            audio.tags.add(frame)
        if cover:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, data=cover))
        if lyrics:
            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang="eng", text=lyrics))
    audio.save()


def fetch_enrichment(client, folder, job, lyrics_enabled=True, cancel=None):
    """Fetch sidecars only: never touch audio files or the shared job manifest."""
    meta = job["metadata"]
    warnings = []
    cover_path = folder / "cover.jpg"
    cover = cover_path.read_bytes() if cover_path.exists() else None
    if not cover and meta.get("release_id"):
        for kind, ident in [
            ("release", meta["release_id"]),
            ("release-group", meta.get("release_group")),
        ]:
            if cancel is not None and cancel.is_set():
                break
            if not ident:
                continue
            try:
                data = client.get(
                    f"https://coverartarchive.org/{kind}/{ident}/front-1200", binary=True
                )
                if data and (
                    data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")
                ):
                    cover = data
                    cover_path.write_bytes(data)
                    break
            except Exception as exc:
                warnings.append(f"Artwork lookup: {exc}")
    if not cover:
        warnings.append("Artwork missing (retry later or place your own JPEG at cover.jpg)")
    lyrics_by_track = []
    for index, track in enumerate(meta["tracks"]):
        if cancel is not None and cancel.is_set():
            break
        lyrics = None
        txt = folder / f"{index + 1:02}.lyrics.txt"
        if txt.exists():
            lyrics = txt.read_text()
        elif lyrics_enabled and meta.get("release_id"):
            try:
                query = urllib.parse.urlencode(
                    {
                        "artist_name": track["artist"],
                        "track_name": track["title"],
                        "album_name": meta["album"],
                        "duration": round(job["toc"]["tracks"][index]["sectors"] / 75),
                    }
                )
                result = client.get("https://lrclib.net/api/get?" + query)
                if result:
                    lyrics = result.get("plainLyrics")
                    if lyrics:
                        txt.write_text(lyrics)
                    if result.get("syncedLyrics"):
                        (folder / f"{index + 1:02}.lrc").write_text(result["syncedLyrics"])
                if not lyrics and not (result or {}).get("instrumental"):
                    warnings.append(f"Lyrics missing: {track['title']}")
            except Exception as exc:
                warnings.append(f"Lyrics {track['title']}: {exc}")
        lyrics_by_track.append(lyrics)
    return cover, lyrics_by_track, warnings


def enrich(client, folder, job, lyrics_enabled=True, fetched=None):
    cover, lyrics_by_track, warnings = (
        fetched if fetched is not None else fetch_enrichment(client, folder, job, lyrics_enabled)
    )
    meta = job["metadata"]
    for index, entry in enumerate(job["files"]):
        lyrics = lyrics_by_track[index]
        target = folder / entry["name"]
        tag_file(target, meta, index, cover, lyrics)
        entry["sha256"] = digest(target)
        save(folder / "job.json", job)
    job["warnings"] = warnings
    save(folder / "job.json", job)
    for warning in warnings:
        say("  " + warning)


IMPORT_SCRIPT = """on run argv
    set audioFile to POSIX file (item 1 of argv) as alias
    tell application "Music"
        set addedTracks to add audioFile
    end tell
end run
"""


def import_music(folder, job, retry_uncertain=False):
    if job.get("format") == "flac" or any(f["name"].endswith(".flac") for f in job["files"]):
        raise ValueError("Music does not support FLAC import. Use ALAC or AAC for Music.")
    if not job["metadata"].get("release_id"):
        say(
            "Audio saved; Music import deferred until the album is identified with retry-metadata --release."
        )
        return
    for entry in job["files"]:
        if entry.get("imported"):
            continue
        if entry.get("import_pending") and not retry_uncertain:
            raise RuntimeError(
                "A previous Music import was interrupted. Check Music for duplicates, then use import-music --retry-uncertain if needed."
            )
        entry["import_pending"] = True
        save(folder / "job.json", job)
        path = str((folder / entry["name"]).resolve())
        event("importing", name=entry["name"])
        try:
            if GUI:
                # The native app owns Automation permission, not its frozen Python helper.
                event("import_request", path=path)
                result = IMPORT_RESULTS.get(timeout=330)
                if result.get("path") != path or not result.get("ok"):
                    raise RuntimeError(result.get("error") or "Music did not confirm this track")
            else:
                run(
                    ["osascript", "-", path],
                    input=IMPORT_SCRIPT,
                    text=True,
                    capture_output=True,
                    timeout=120,
                )
        except (subprocess.TimeoutExpired, queue.Empty) as exc:
            raise RuntimeError(
                "Music import timed out. Your audio is safe. Check Music and any permission "
                "prompt before retrying; this track may already have been added."
            ) from exc
        entry["imported"] = True
        entry["import_pending"] = False
        save(folder / "job.json", job)
    say("Added to Music ✓")
    job.pop("postprocess_error", None)
    save(folder / "job.json", job)


def load_job(folder):
    job = json.loads((folder / "job.json").read_text())
    # Jobs contain only basenames so metadata can never escape the album directory.
    for item in job.get("files", []):
        if Path(item["name"]).name != item["name"]:
            raise ValueError("Invalid filename in job")
    return job


def check_dependencies():
    missing = [
        x
        for x in ("ffmpeg", "ffprobe", "cd-paranoia", "diskutil", "osascript")
        if not shutil.which(x)
    ]
    libdiscid()  # Loading a library does not read the drive.
    if missing:
        raise RuntimeError("Missing tools: " + ", ".join(missing))


def process_disc(device, args):
    if device not in audio_devices():
        raise RuntimeError("Selected device is not a mounted audio CD; refusing to access it")
    toc = read_toc(device)
    audio_format = getattr(args, "format", "alac")
    bitrate = getattr(args, "aac_bitrate", 256)
    suffix = (
        ""
        if audio_format == "alac"
        else "-" + audio_format + (f"-{bitrate}" if audio_format == "aac" else "")
    )
    folder = args.output / (toc["id"] + suffix)
    folder.mkdir(parents=True, exist_ok=True)
    jobfile = folder / "job.json"
    client = Client()
    if jobfile.exists():
        job = load_job(folder)
        if job["toc"] != toc:
            raise RuntimeError("Saved disc layout differs; refusing to overwrite")
    else:
        try:
            meta = identify(client, toc, args.release)
        except Exception as exc:
            if args.release:
                raise
            say(f"Metadata unavailable: {exc}; saving audio for later identification.")
            meta = None
        job = {
            "toc": toc,
            "metadata": meta or unknown(toc),
            "files": [],
            "audio_complete": False,
            "format": audio_format,
            "aac_bitrate": bitrate if audio_format == "aac" else None,
        }
        save(jobfile, job)
    if job.get("audio_complete"):
        for entry in job["files"]:
            if (
                not (folder / entry["name"]).exists()
                or digest(folder / entry["name"]) != entry["sha256"]
            ):
                raise RuntimeError(
                    "An existing audio file changed or is missing; refusing to skip or overwrite"
                )
        say(
            f"Already ripped: {job['metadata']['album']}. Use retry-metadata/import-music for unfinished tasks."
        )
        return
    say(f"Found: {job['metadata']['artist']} — {job['metadata']['album']}")
    event("album", metadata=job["metadata"], folder=str(folder))
    if STOP.is_set():
        say("Stopped before extraction; the disc was left inserted.")
        return False
    # Normal (non-force) unmount: an app holding the disc can prevent this.
    if read_toc(device) != toc:
        raise RuntimeError("Disc changed during identification; refusing to rip")
    run(["diskutil", "unmountDisk", device], capture_output=True, timeout=30)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="metadata")
    cancel_metadata = threading.Event()
    say("Fetching artwork and lyrics in the background…")
    metadata = pool.submit(
        fetch_enrichment, Client(), folder, copy.deepcopy(job), not args.no_lyrics, cancel_metadata
    )
    try:
        for index, track in enumerate(toc["tracks"]):
            if index < len(job["files"]):
                entry = job["files"][index]
                if digest(folder / entry["name"]) != entry["sha256"]:
                    raise RuntimeError("Previously saved track changed; refusing to overwrite")
                continue
            say(
                f"Ripping {index + 1}/{len(toc['tracks'])}: {job['metadata']['tracks'][index]['title']}"
            )
            wav = folder / f"{index + 1:02}.partial.wav"
            target = (
                folder
                / f"{index + 1:02} - {safe(job['metadata']['tracks'][index]['title'])}.{FORMATS[audio_format][0]}"
            )
            if target.exists():
                raise RuntimeError(
                    f"Unrecorded file exists: {target}. Move it aside before resuming."
                )
            event(
                "track",
                number=index + 1,
                total=len(toc["tracks"]),
                title=job["metadata"]["tracks"][index]["title"],
                partial=str(wav),
                expected=track["sectors"] * 2352,
            )
            # -X aborts on unrecoverable skips, instead of accepting damaged audio.
            with (folder / f"{index + 1:02}.rip.log").open("ab") as log:
                run(
                    ["cd-paranoia", "-d", device, "-X", "-e", "--", str(track["number"]), wav],
                    stdout=log,
                    stderr=log,
                    timeout=1800,
                )
            import wave

            with wave.open(str(wav)) as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (
                    2,
                    2,
                    44100,
                ):
                    raise RuntimeError("Unexpected CD audio format")
                if audio.getnframes() != track["sectors"] * 588:
                    raise RuntimeError(
                        "Extracted track length differs from CD TOC; WAV retained for review"
                    )
            encode(wav, target, audio_format, bitrate)
            tag_file(target, job["metadata"], index)
            job["files"].append({"name": target.name, "sha256": digest(target), "imported": False})
            save(jobfile, job)
            wav.unlink()  # Only this tool's verified temporary extraction.
        job["audio_complete"] = True
        save(jobfile, job)
    except BaseException:
        # Make the disc visible again after failure/Control-C; never eject on read failure.
        cancel_metadata.set()
        try:
            subprocess.run(["diskutil", "mountDisk", device], capture_output=True, timeout=30)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        raise
    try:
        if not args.no_eject:
            run(["diskutil", "eject", device], capture_output=True, timeout=30)
            say("Audio saved and checked; CD ejected.")
        else:
            run(["diskutil", "mountDisk", device], capture_output=True, timeout=30)
    finally:
        # The drive is released before waiting for any remaining network requests.
        event("enriching", folder=str(folder))
        pool.shutdown(wait=True)
    say("Embedding artwork and lyrics…")
    try:
        enrich(client, folder, job, not args.no_lyrics, fetched=metadata.result())
        if not args.no_music and audio_format != "flac":
            import_music(folder, job)
    except Exception as exc:
        job["postprocess_error"] = str(exc)
        save(jobfile, job)
        say(f"Audio safe; post-processing needs attention: {exc}")
        say(f"Use retry-metadata or import-music with: {folder}")
        event("attention", message=str(exc))
    say(f"Saved: {folder}")
    event(
        "complete",
        music_requested=not args.no_music and audio_format != "flac",
        error=job.get("postprocess_error"),
        folder=str(folder),
        warnings=job.get("warnings", []),
        imported=sum(bool(item.get("imported")) for item in job["files"]),
        total=len(job["files"]),
    )
    return not args.no_eject


def watch(args):
    seen = audio_devices()
    say("Waiting for a NEW CD. Any disc currently inserted is ignored. Ctrl-C stops.")
    event("waiting")
    while not STOP.is_set():
        devices = audio_devices()
        seen.intersection_update(devices)
        for device in sorted(devices - seen):
            if STOP.is_set():
                break
            seen.add(device)  # Failures never trigger an endless automatic retry.
            try:
                if process_disc(device, args):
                    # A new disc may be inserted on the SAME device while tags are fetched.
                    seen.discard(device)
            except Exception as exc:
                say(
                    f"Needs attention ({device}): {exc}. Reinsert after resolving, or use rip explicitly."
                )
                event("attention", message=str(exc))
            event("waiting")
        time.sleep(3)


def status(output):
    jobs = sorted(output.glob("*/job.json"))
    if not jobs:
        say("No jobs yet.")
    for path in jobs:
        job = load_job(path.parent)
        count = len(job["files"])
        total = len(job["toc"]["tracks"])
        imported = sum(bool(f.get("imported")) for f in job["files"])
        say(f"{job['metadata']['artist']} — {job['metadata']['album']}")
        say(f"  Audio: {count}/{total} saved; Music: {imported}/{total} imported")
        if not job.get("audio_complete") and count < total:
            partial = path.parent / f"{count + 1:02}.partial.wav"
            if partial.exists():
                expected = job["toc"]["tracks"][count]["sectors"] * 2352
                percent = min(100, max(0, partial.stat().st_size - 44) * 100 / expected)
                say(f"  Track {count + 1}: {percent:.0f}% written (may be active or interrupted)")
        for warning in job.get("warnings", []):
            say("  " + warning)
        say(f"  {path.parent}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"cd-rip {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check dependencies only; never access drive or Music")
    p = sub.add_parser("status", help="Read saved progress; no drive or Music access")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    for command in ("watch", "rip"):
        p = sub.add_parser(command)
        p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
        p.add_argument("--no-music", action="store_true")
        p.add_argument("--no-eject", action="store_true")
        p.add_argument("--no-lyrics", action="store_true")
        p.add_argument(
            "--format", choices=FORMATS, default="alac", help="Output codec (default: alac)"
        )
        p.add_argument("--aac-bitrate", type=int, choices=(128, 192, 256, 320), default=256)
        p.add_argument("--release", help="MusicBrainz release UUID (for explicit identification)")
        if command == "rip":
            p.add_argument(
                "--device", help="Mounted audio CD, e.g. /dev/disk4; default: sole audio CD"
            )
    p = sub.add_parser(
        "retry-metadata",
        help="Repair tags without a CD; already imported Music copies may need refreshing",
    )
    p.add_argument("album", type=Path)
    p.add_argument("--release")
    p.add_argument("--no-lyrics", action="store_true")
    p = sub.add_parser(
        "import-music", help="Import saved files that have not already been imported"
    )
    p.add_argument("album", type=Path)
    p.add_argument("--retry-uncertain", action="store_true")
    args = parser.parse_args()
    if GUI:
        threading.Thread(target=read_controls, daemon=True).start()
    if args.command == "status":
        status(args.output.expanduser().resolve())
        return
    if args.command == "doctor":
        check_dependencies()
        say("Dependencies ready. No CD, drive, or Music access performed.")
        return
    # One process across all output locations, so two watchers cannot compete for a drive.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "process.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another cd-rip command is running")
        # Also respect a running copy of the pre-package CLI on this machine.
        legacy_lock = None
        legacy_path = os.environ.get("CD_RIP_LEGACY_LOCK")
        if legacy_path and Path(legacy_path).exists():
            legacy_lock = open(legacy_path, "a")
            try:
                fcntl.flock(legacy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                legacy_lock.close()
                raise RuntimeError(
                    "The previous CLI is still ripping. Let it finish before starting the app."
                ) from None
        if args.command in ("retry-metadata", "import-music"):
            folder = args.album.expanduser().resolve()
            job = load_job(folder)
            if not job.get("audio_complete"):
                raise RuntimeError(
                    "Audio extraction is incomplete; resume with the original CD first"
                )
            if args.command == "import-music":
                import_music(folder, job, args.retry_uncertain)
            else:
                client = Client()
                if args.release or not job["metadata"].get("release_id"):
                    meta = identify(client, job["toc"], args.release)
                    if not meta:
                        raise RuntimeError(
                            "No match: use --release with the correct MusicBrainz release UUID"
                        )
                    job["metadata"] = meta
                    save(folder / "job.json", job)
                enrich(client, folder, job, not args.no_lyrics)
                say(
                    "Tags updated. If Music copied these files, its copies need separate refreshing."
                )
            return
        check_dependencies()
        args.output = args.output.expanduser().resolve()
        if args.command == "watch":
            watch(args)
        else:
            devices = audio_devices()
            if not args.device and len(devices) != 1:
                raise RuntimeError(f"Expected one audio CD, found {len(devices)}; specify --device")
            process_disc(args.device or next(iter(devices)), args)


def entrypoint():
    try:
        main()
    except KeyboardInterrupt:
        say("Stopped. Saved tracks kept for resuming.")
        sys.exit(130)
    except Exception as exc:
        say(f"Error: {exc}")
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            say(str(exc.stderr))
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
