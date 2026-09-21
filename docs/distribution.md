# Distribution status

Version 0.2.0 is a source-build preview. No signed or notarized app download is offered.
The personal build is ad-hoc signed; it is not a consumer-ready installer.

## Before publishing an app download

- Select a project license and include all dependency licenses and notices.
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
- Enable the CI template in `docs/ci.yml` with GitHub credentials that can update
  workflows. CI is not currently enabled.

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
