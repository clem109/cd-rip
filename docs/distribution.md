# Distribution status

Version 0.2.0 is a source-build preview. No signed or notarized app download is offered.
The personal build is ad-hoc signed; it is not a consumer-ready installer.

## Before publishing an app download

- The project uses GPL-3.0-or-later. Builds include dependency notices and runtime versions.
- Supply corresponding source where required for bundled GPL components. Mutagen is
  GPL-2.0-or-later. Python and the PyInstaller bootloader have separate license terms;
  review the complete frozen dependency inventory before shipping it.
- Decide whether to retain the Homebrew prerequisites or bundle the external tools.
  Bundling FFmpeg, libdiscid or libcdio-paranoia requires a further license and dylib
  audit; FFmpeg's obligations depend on its build configuration.
- Build each supported architecture using a Python runtime compatible with the
  advertised minimum macOS version. The Swift deployment target alone does not prove
  Python compatibility. Currently tested on Apple Silicon with macOS 15.3.1.
- Sign nested executables and libraries, then the app, using Developer ID and hardened
  runtime with the required entitlements. Ad-hoc `codesign --deep` is only for local use.
- Submit with Apple's `notarytool`, staple the accepted ticket, and verify with
  Gatekeeper on a clean Mac. Do not tell users to disable Gatekeeper.
- Test a clean install, folder and Music permissions, every format, errors, restart,
  incomplete-rip recovery and Music import using a real CD drive. Automated tests use
  generated audio; they are not a substitute for drive compatibility testing.
- CI is enabled in `.github/workflows/ci.yml`: two Python versions, lint, tests,
  source/wheel packaging, and a native app build. Unsigned app bundles are not published.

## Signed release workflow

`mac/release.py` requires a **Developer ID Application** certificate with its private key
installed in your login Keychain, and an existing `notarytool` Keychain profile. Create
these through your Apple Developer account; don't put certificates, passwords or API keys
in the repo or in chat. Apple Developer enrollment/agreements must be handled by the owner.

Once those are configured, with a clean committed checkout:

```sh
.venv/bin/python mac/release.py \
  --identity 'Developer ID Application: Your Name (TEAMID)' \
  --notary-profile 'cd-rip-notary'
```

The script signs the native app and frozen engine with hardened runtime, prepares source
archives, submits to Apple, requires an Accepted response, staples and validates the ticket,
and runs Gatekeeper verification. Only after success does it create a versioned directory
under `dist/` with the app ZIP, matching source archive, and SHA-256 checksums. It never
publishes to GitHub automatically. Publish both archives together after clean-Mac testing.

The app requests Apple-events permission for Music. Only the helper has the
disable-library-validation entitlement because it loads separately installed Homebrew
libdiscid. No system security setting is changed. External tools still require Homebrew.

Signing/notarization cannot be exercised without those Apple credentials; script preflight
and rejection handling are covered by local tests, but an accepted notarization is required
before calling a download release-ready.

See Apple's [distribution-signing guide](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/)
and [notarization guide](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Portable versus personal builds

`python mac/build.py` produces an empty machine-specific configuration and defaults to
`~/Music/CD Rip/` at runtime. `--local-config` opts into the developer checkout's archive
and legacy lock. Never publish that personal build. Existing app preferences are retained
when updating locally.

The public source and screenshot do not include ripped audio, downloaded lyrics, recovery
records, credentials or build artifacts. The screenshot's album cover is illustrative
third-party artwork, not a separately licensed application asset.
