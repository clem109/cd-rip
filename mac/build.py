"""Build a local, ad-hoc-signed Mac app. Does not launch it or access a CD."""

import argparse
import importlib.metadata
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


def copy_notices(resources):
    notices = resources / "Licenses"
    notices.mkdir()
    shutil.copy2(ROOT / "LICENSE", notices / "CD-Rip-GPL-3.0.txt")
    shutil.copy2(ROOT / "THIRD_PARTY.md", notices / "THIRD_PARTY.md")
    versions = {"python": sys.version.split()[0]}
    for name in ("mutagen", "pyinstaller", "pyinstaller-hooks-contrib"):
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        licenses = [
            p for p in distribution.files or [] if p.name.upper().startswith(("LICENSE", "COPYING"))
        ]
        if not licenses:
            raise RuntimeError(f"No installed license found for {name}")
        for index, path in enumerate(licenses):
            shutil.copy2(distribution.locate_file(path), notices / f"{name}-{index}-{path.name}")
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "LICENSE.txt",
    ]
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is None:
        raise RuntimeError(
            "CPython LICENSE.txt is missing from this runtime; use a complete Python installation"
        )
    shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    (resources / "runtime-versions.json").write_text(json.dumps(versions, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-config",
        action="store_true",
        help="Use this checkout's existing archive and legacy lock (personal builds only)",
    )
    parser.add_argument(
        "--sign-identity", help="Developer ID Application identity (otherwise ad-hoc signing)"
    )
    args = parser.parse_args()
    if args.sign_identity and args.local_config:
        parser.error("Distribution signing cannot include personal checkout paths")
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
            *(
                [
                    "--codesign-identity",
                    args.sign_identity,
                    "--osx-entitlements-file",
                    str(ROOT / "mac" / "engine.entitlements.plist"),
                ]
                if args.sign_identity
                else []
            ),
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
        copy_notices(resources)
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
        if args.local_config and (ROOT / "library").exists():
            config["archive"] = str(ROOT / "library")
        if args.local_config and (ROOT / ".lock").exists():
            config["legacy_lock"] = str(ROOT / ".lock")
        (resources / "config.json").write_text(json.dumps(config))
        info = {
            "CFBundleIdentifier": "com.clem109.cdrip",
            "CFBundleName": "CD Rip",
            "CFBundleDisplayName": "CD Rip",
            "CFBundleExecutable": "CD Rip",
            "CFBundleIconFile": "AppIcon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.2.0",
            "CFBundleVersion": "2",
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "CD Rip adds your finished albums to Music.",
        }
        with (contents / "Info.plist").open("wb") as file:
            plistlib.dump(info, file)
        if args.sign_identity:
            run(
                "codesign",
                "--force",
                "--timestamp",
                "--options",
                "runtime",
                "--entitlements",
                ROOT / "mac" / "engine.entitlements.plist",
                "--sign",
                args.sign_identity,
                resources / "engine" / "cd-rip-engine",
            )
            run(
                "codesign",
                "--force",
                "--timestamp",
                "--options",
                "runtime",
                "--entitlements",
                ROOT / "mac" / "app.entitlements.plist",
                "--sign",
                args.sign_identity,
                bundle,
            )
        else:
            run("codesign", "--force", "--deep", "--sign", "-", bundle)
        run("codesign", "--verify", "--deep", "--strict", bundle)
        # Keep the last built app recoverable while replacing it.
        if destination.exists():
            backup = destination.with_name(f"CD Rip.previous-{time.time_ns()}.app")
            destination.rename(backup)
        shutil.copytree(bundle, destination, symlinks=True)
    print(destination)


if __name__ == "__main__":
    main()
