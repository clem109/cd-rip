"""Build a local, ad-hoc-signed Mac app. Does not launch it or access a CD."""

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    destination = ROOT / "dist" / "CD Rip.app"
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cd-rip-build-") as temp:
        work = Path(temp)
        swift_flags = []
        # Some CLT upgrades leave an obsolete duplicate SwiftBridging module map.
        # Hide only that stale map for this build using a virtual filesystem overlay;
        # never edit the installed toolchain.
        developer = Path(subprocess.check_output(["xcode-select", "-p"], text=True).strip())
        old_map = developer / "usr/include/swift/module.modulemap"
        new_map = developer / "usr/include/swift/bridging.modulemap"
        if (
            old_map.exists()
            and new_map.exists()
            and all("module SwiftBridging" in p.read_text() for p in (old_map, new_map))
        ):
            empty = work / "empty.modulemap"
            empty.write_text("// Obsolete duplicate suppressed only in this build.\n")
            overlay = work / "toolchain-overlay.json"
            overlay.write_text(
                json.dumps(
                    {
                        "version": 0,
                        "roots": [
                            {"type": "file", "name": str(old_map), "external-contents": str(empty)}
                        ],
                    }
                )
            )
            swift_flags = [
                "-vfsoverlay",
                str(overlay),
                "-Xcc",
                "-ivfsoverlay",
                "-Xcc",
                str(overlay),
            ]
        run(
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--name",
            "cd-rip-engine",
            "--paths",
            ROOT / "src",
            "--collect-all",
            "mutagen",
            "--distpath",
            work / "engine-dist",
            "--workpath",
            work / "engine-work",
            "--specpath",
            work,
            ROOT / "mac" / "engine.py",
        )
        bundle = work / "CD Rip.app"
        contents = bundle / "Contents"
        resources = contents / "Resources"
        executable = contents / "MacOS"
        resources.mkdir(parents=True)
        executable.mkdir()
        shutil.copytree(work / "engine-dist" / "cd-rip-engine", resources / "engine")
        run(
            "xcrun",
            "swiftc",
            *swift_flags,
            "-swift-version",
            "5",
            "-O",
            "-target",
            "arm64-apple-macosx13.0"
            if os.uname().machine == "arm64"
            else "x86_64-apple-macosx13.0",
            "-module-cache-path",
            work / "module-cache",
            ROOT / "mac" / "App.swift",
            "-o",
            executable / "CD Rip",
        )
        config = {}
        run(
            "xcrun",
            "swiftc",
            *swift_flags,
            "-module-cache-path",
            work / "module-cache",
            ROOT / "mac" / "Icon.swift",
            "-o",
            work / "make-icon",
        )
        run(work / "make-icon", work / "AppIcon.iconset")
        run("iconutil", "-c", "icns", work / "AppIcon.iconset", "-o", resources / "AppIcon.icns")
        if (ROOT / "library").exists():
            config["archive"] = str(ROOT / "library")
        if (ROOT / ".lock").exists():
            config["legacy_lock"] = str(ROOT / ".lock")
        (resources / "config.json").write_text(json.dumps(config))
        info = {
            "CFBundleIdentifier": "com.clem109.cdrip",
            "CFBundleName": "CD Rip",
            "CFBundleDisplayName": "CD Rip",
            "CFBundleExecutable": "CD Rip",
            "CFBundleIconFile": "AppIcon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "CD Rip adds your finished lossless albums to Music.",
        }
        with (contents / "Info.plist").open("wb") as file:
            plistlib.dump(info, file)
        run("codesign", "--force", "--deep", "--sign", "-", bundle)
        # Keep the last built app recoverable while replacing it.
        if destination.exists():
            backup = destination.with_name(f"CD Rip.previous-{time.time_ns()}.app")
            destination.rename(backup)
        shutil.copytree(bundle, destination, symlinks=True)
    print(destination)


if __name__ == "__main__":
    main()
