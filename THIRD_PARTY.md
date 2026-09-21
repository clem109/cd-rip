# Third-party components

CD Rip code is copyright 2026 Clement Venard and contributors, licensed under
GPL-3.0-or-later. Copyright in each dependency remains with its respective authors.

| Component | Use | License / upstream |
| --- | --- | --- |
| Mutagen | Bundled metadata library | [GPL-2.0-or-later](https://github.com/quodlibet/mutagen) |
| CPython | Bundled runtime | [PSF License and included notices](https://docs.python.org/3/license.html) |
| PyInstaller bootloader | Bundled executable launcher | [GPL-2.0 with bootloader exception](https://pyinstaller.org/en/stable/license.html) |
| PyInstaller hooks | Build-time integration | [GPL-2.0-or-later with exception](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |
| FFmpeg / ffprobe | External Homebrew tools, not bundled | [LGPL/GPL depending on build](https://ffmpeg.org/legal.html) |
| libdiscid | External Homebrew library, not bundled | [LGPL](https://github.com/metabrainz/libdiscid) |
| libcdio-paranoia | External CD-reading tool, not bundled | [GPL](https://www.gnu.org/software/libcdio/) |

The build copies installed Mutagen/PyInstaller notices and CPython's license into
`Contents/Resources/Licenses` and records the bundled versions. Python's bundled
libraries may have additional terms described by its license file. Review the final
binary inventory for each release; this table is not a substitute for that review.

Binary releases must be accompanied by the matching CD Rip source and the exact
Mutagen source distribution, including their license notices. The release script
collects these automatically. If modifying dependencies or adding bundled native
libraries, update the source bundle and notices before distribution.

Album art, lyrics and metadata retrieved from MusicBrainz, Cover Art Archive and
LRCLIB retain their respective rights and terms. Their inclusion in an example
screenshot does not grant a license to reuse that artwork independently.
