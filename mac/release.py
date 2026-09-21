"""Build, Developer-ID sign, notarize and package. Never publishes automatically."""

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, check=True, text=True, capture_output=capture
    )
    return result.stdout.strip() if capture else None


def validate_identity(identity, identities):
    if not identity.startswith("Developer ID Application:") or identity not in identities:
        raise ValueError(
            "A valid Developer ID Application certificate and private key are required"
        )


def require_accepted(response):
    if response.get("status") != "Accepted":
        raise RuntimeError(
            f"Notarization was not accepted: {response.get('status', 'unknown')}. "
            f"Inspect notary submission {response.get('id', 'unknown')}."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True, help="Full Developer ID Application identity")
    parser.add_argument(
        "--notary-profile", required=True, help="Existing notarytool Keychain profile"
    )
    args = parser.parse_args()
    validate_identity(
        args.identity, run("security", "find-identity", "-v", "-p", "codesigning", capture=True)
    )
    if run("git", "status", "--porcelain", capture=True):
        raise RuntimeError("Commit changes before building a traceable release")
    commit = run("git", "rev-parse", "HEAD", capture=True)
    run(sys.executable, ROOT / "mac/build.py", "--sign-identity", args.identity)
    app = ROOT / "dist/CD Rip.app"
    resources = app / "Contents/Resources"
    if json.loads((resources / "config.json").read_text()):
        raise RuntimeError("Refusing to distribute a personal build")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    versions = json.loads((resources / "runtime-versions.json").read_text())
    name = f"CD-Rip-{info['CFBundleShortVersionString']}-{os.uname().machine}"
    output = ROOT / "dist" / name
    if output.exists():
        raise RuntimeError(f"Release output already exists: {output}; preserve or move it first")
    with tempfile.TemporaryDirectory(prefix="cd-rip-release-") as temporary:
        work = Path(temporary)
        sources = work / "sources"
        sources.mkdir()
        run(
            "git",
            "archive",
            "--format=tar.gz",
            "--prefix=cd-rip/",
            "-o",
            sources / "cd-rip-source.tar.gz",
            commit,
        )
        run(
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--no-binary=:all:",
            f"mutagen=={versions['mutagen']}",
            "--dest",
            sources,
        )
        shutil.copy2(ROOT / "LICENSE", sources / "LICENSE")
        shutil.copy2(ROOT / "THIRD_PARTY.md", sources / "THIRD_PARTY.md")
        (sources / "build.json").write_text(
            json.dumps({"commit": commit, "versions": versions}, indent=2)
        )
        upload = work / "notarization.zip"
        run("ditto", "-c", "-k", "--keepParent", app, upload)
        response = json.loads(
            run(
                "xcrun",
                "notarytool",
                "submit",
                upload,
                "--keychain-profile",
                args.notary_profile,
                "--wait",
                "--output-format",
                "json",
                capture=True,
            )
        )
        require_accepted(response)
        run("xcrun", "stapler", "staple", app)
        run("xcrun", "stapler", "validate", app)
        run("codesign", "--verify", "--deep", "--strict", app)
        run("spctl", "--assess", "--type", "execute", "--verbose=2", app)
        package = work / "package"
        package.mkdir()
        run("ditto", "-c", "-k", "--keepParent", app, package / f"{name}.zip")
        run("tar", "-czf", package / f"{name}-sources.tar.gz", "-C", work, "sources")
        sums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in package.iterdir()}
        (package / "SHA256SUMS.json").write_text(json.dumps(sums, indent=2) + "\n")
        shutil.copytree(package, output)
    print(f"Notarized package and corresponding source: {output}")
    print(
        "Test on a clean Mac, then publish both archives together. Nothing has been uploaded to GitHub."
    )


if __name__ == "__main__":
    main()
