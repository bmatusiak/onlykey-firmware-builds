# OnlyKey developer firmware

Unsigned OnlyKey firmware, built from each release's pinned commits with the
original Arduino 1.6.5 / Teensyduino 1.27 toolchain.

**These will not load on a production key.** A production bootloader accepts
signed firmware only. A developer bootloader refuses signed firmware and accepts
only an unsigned build like these. If you do not have a developer key, you want
the signed releases in [`signed_firmware/`](signed_firmware/) instead.

Browse the builds: **https://bmatusiak.github.io/onlykey-firmware-builds/**

## Why this exists

`ok-rn` can be tested against every firmware release, but only on the *soft*
key — `OKEMU_VERSION` rebuilds the Android native library from a release's
pinned source. The hard key runs whatever is on it, and that is one version, so
half the version matrix has never been tested on real hardware.

This closes that. Every release in node-onlykey-lib's release table becomes a
`.hex` a developer key will take, and so does **`latest`**: the working tree of
the checkouts beside this repo, rebuilt on every sweep.

## What is here

    developer_firmware/*.hex          every buildable combination
    developer_firmware/logs/          one log per build, plus one per sweep
    developer_firmware/index.json     every variant built, with pins and sha256
    developer_firmware/matrix.json    every combination and what can exist
    developer_firmware/failures.json  what failed and why, so it is retried
    signed_firmware/<tag>/*.txt       trustcrypto's signed images, each matching its published SHA-256
    signed_firmware/index.json        every release asset: our hash, the published hash, the verdict
    package.json                      node-onlykey-lib, pinned by commit: the release table
    docs/                             the published page
    build/                            the builder

The artefacts, their logs and the matrix state all live **in the repo**, which
is what makes the work portable. Clone this anywhere with Docker, run a sweep,
and it builds only what is missing. Bump the lib and it builds only the releases
whose pins moved. Nothing is ever rebuilt to get back to where you were - except
`latest`, which is rebuilt every time.

## Building

Needs Linux with Docker, and the component checkouts beside this one:

    <workspace>/
      onlykey-firmware-builds/     this repo
      OnlyKey-Firmware/            github.com/bm-ok/OnlyKey-Firmware
      libraries/                   github.com/bm-ok/0c-coder-libraries
      arduino-1.6.5-r5-teensy_127/ github.com/bm-ok/arduino-1.6.5-r5-teensy_127

`libraries` also needs `trustcrypto` as a second remote — v2.1.2's pin exists
only there, on branch `remove-touchsense`, so the fork alone cannot build that
release:

    git -C libraries remote add trustcrypto https://github.com/trustcrypto/libraries
    git -C libraries fetch trustcrypto

v3.1.0's pins live on `trustcrypto`'s `release-3.1.0` branch, in **both**
checkouts. A fetch adds objects and never moves the checkout:

    git -C OnlyKey-Firmware remote add trustcrypto https://github.com/trustcrypto/OnlyKey-Firmware
    for r in libraries OnlyKey-Firmware; do git -C $r fetch trustcrypto release-3.1.0; done

That branch is re-squashed as the release PRs move; when the lib re-pins v3.1.0,
fetch again.

Then:

    npm install            # node-onlykey-lib; prints the release table it got
    npm run versions       # the table, as the builder reads it
    npm run sweep          # the whole matrix, resuming; survives logout
    npm run status         # live status page on :8090
    npm run list           # what a sweep would build, skip or rebuild, and why
    npm run gates          # check the build gates against every pin, no compiling

One release, or one variant:

    python3 build/build.py --only v3.0.2
    python3 build/build.py --only v3.0.2 --models duo --builds prod

### On a Raspberry Pi

The pinned `arm-none-eabi-gcc` 4.8.4 is an **x86-64 Linux binary** — there is no
aarch64 build of it and there never was. So a Pi can only run it emulated, and
`arduino-1.6.5-r5-teensy_127` carries `pi-setup.sh` and `Dockerfile.pi` for
exactly that. Run `./pi-setup.sh` there first.

Installing a modern aarch64-hosted `arm-none-eabi` instead would be fast, native
and would quietly produce a different binary — which defeats the reason for
pinning commits at all.

Measured on a 4-core Pi with 1.8 GB RAM: a full sweep is 28 builds in about
5h30m, averaging 14 minutes each.

## Signed releases and their hashes

`npm run signed` (`build/signed.py`) reads every release on
[trustcrypto/OnlyKey-Firmware](https://github.com/trustcrypto/OnlyKey-Firmware/releases),
downloads each firmware asset, hashes it, and compares that with the SHA-256
trustcrypto typed into the release notes. `signed_firmware/index.json` records
all three - ours, the published value with the exact line it came from, and
GitHub's own asset digest (null for every OnlyKey asset: they predate it).

A file is **verified** only when our hash equals a 64-digit value published on
its release. Only verified signed images are kept in `signed_firmware/`, and only
those are offered on the page, where the browser fetches each one, hashes it
again and hands it over only on a match. Found on 2026-09-30:

- `Signed_OnlyKey_Beta7_IN_TRVL_Color.txt` is **not** verified: Beta 7's notes
  publish a 63-digit value for it - ours with its last digit missing. Not kept,
  not offered.
- Beta 2 and Beta 0 publish no hashes (Beta 2 points at the old quick-start
  guide), so their images are "unpublished".
- Beta 5's hashes are published with a space typed inside each; they verify
  with the whitespace removed, and the record says so.
- The lib names one image per release (the STD one); every one of them is
  here and verified. The IN TRVL images and Beta 7's signed set are here too.

`signed_firmware/.gitattributes` marks the files `-text`. They are LF-only, and
a Windows checkout with `core.autocrlf` was writing them back with CRLF - 15
bytes longer, and a different hash from the one trustcrypto published.

Then `npm run docs` carries both - these and the developer builds' `sha256` -
into `docs/data.json`.

## What a variant is

Two switches, both set **explicitly** and never inherited from the pin:

| | | |
|---|---|---|
| `build` | `test` / `prod` | the `DEBUG` gate |
| `model` | `classic` / `duo` | the `DEFINED_HWID` override |

Inheriting would be wrong, because the committed flags are not a statement about
a release. `DEBUG` is on in five of nine pins and off in four, in no pattern —
v3.0.3 and v3.0.4 would ship with a serial console open. Those are not release
decisions, they are whatever was in the tree when the tag was cut. What a pin
contributes is **source**, not configuration.

`DEBUG` also decides what the firmware calls itself, with no help from the
builder — `#ifdef DEBUG` picks `-test` or `-prod` for `OKversionkeyword`, and
`node-onlykey-lib` reads exactly that for `capabilities().debugConsole`. So a
build that lies about its own gate is visible from the app.

A variant a pin cannot express is **refused**, not approximated. There is no DUO
build of v2.1.0 — the DUO postdates it — and emitting a classic image under a
filename saying DUO would be worse than emitting nothing.

## Things that were measured

- **`DEFINED_HWID` names `OK_HW_DUO` only on the 3.0 line.** It names
  `OK_HW_COLOR` at v2.1.2 and is absent at v2.1.1, v2.1.0 and v0.2-beta.8. Those
  eight combinations are refused with the reason.
- **`KEYLAYOUTS_DEBUG_BUILD` exists at no pin.** The "debug builds type US
  English only" behaviour is a property of the working tree, not of any release.
- **No build is byte-reproducible.** Teensyduino stamps the build epoch into
  `ResetHandler`'s literal pool as `TIME_T`, at `0x37C`, to seed the RTC. Two
  builds of one variant differ in exactly those two bytes and nowhere else.
- **Six of nine `libraries` pins verify against upstream `<release>-prod` tags**,
  after adding `trustcrypto` as a second remote.
- **The IN TRVL edition cannot be built by flipping its flag.** See
  [the finding](FINDING-the-travel-edition-cannot-be-built-by-flipping-its-flag.md).

## Where the releases come from

The table is **node-onlykey-lib**'s (`require('node-onlykey-lib/versions')`),
pinned by full commit in `package.json` exactly as the emulator pins it.
`build/versions.js` is the one crossing into Python; `build/pins.py` is what
`build.py`, `make-docs.py` and `dryrun.py` read. This repo keeps no copy of the
table - it used to, and the copy drifted: it still built v3.0.5 after the lib
dropped it, and never had v3.1.0.

- **Adding a release, or a re-pin**: bump the lib's commit in `package.json`,
  `npm install`, `npm run sweep`. An image counts as built only if it was built
  at the release's *current* pins, so a moved pin is rebuilt under the same
  name - it used to be skipped by name, which would have published the new pins
  over the old image.
- **`latest`** is the working tree: whatever `libraries` and `OnlyKey-Firmware`
  are checked out beside this repo, uncommitted edits included. It is rolling -
  every sweep rebuilds it and replaces the previous images; git history keeps
  the old ones. Its images record the commits they were built from, and whether
  the tree was dirty.
- **Pre-release**: a pinned release newer than every signed one - today v3.1.0 -
  is labelled pre-release on the page until the lib's row names its signed image.
- **Compatibility**: the page shows the lib's `compatibilityOf(v).capabilities`
  for each release. That is the lib's model of what a signed build reports, not
  something probed from the `.hex`.

There is no `npm run update` any more. It hard-reset the sibling checkouts to
`0c-coder/master` to rebuild v3.0.5 - which on the build Pi were the bench
key's `bench-worktree` checkouts. The release it served is gone, and `latest`
builds the tree without ever moving it.

## Not answered yet

**How an unsigned image gets onto a developer key.** `halfkay_flash.py` in the
toolchain repo suggests HalfKay over a USB cable to the build host, rather than
the app doing it — but nothing in any repo here confirms which container format
that bootloader expects. Worth settling before relying on these in a test loop.
