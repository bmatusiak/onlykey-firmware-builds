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

This closes that. Every release in `ok-versions.json` becomes a `.hex` a
developer key will take.

## What is here

    developer_firmware/*.hex          28 images, every buildable combination
    developer_firmware/logs/          one log per build, plus one per sweep
    developer_firmware/index.json     every variant built, with pins and sha256
    developer_firmware/matrix.json    the 36 combinations and what can exist
    developer_firmware/failures.json  what failed and why, so it is retried
    signed_firmware/*.txt             the official signed releases, for reference
    ok-versions.json                  the pins, and the filenames built from them
    docs/                             the published page
    build/                            the builder

The artefacts, their logs and the matrix state all live **in the repo**, which
is what makes the work portable. Clone this anywhere with Docker, run a sweep,
and it builds only what is missing. Add a pin and it builds that release alone.
Nothing is ever rebuilt to get back to where you were.

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

Then:

    npm run sweep          # the whole matrix, resuming; survives logout
    npm run status         # live status page on :8090
    npm run list           # what would be built
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

## Adding a release

Add its pins to `ok-versions.json`, then `npm run sweep`. Everything already
built is skipped; only the new release compiles. `ok-versions.json` gains a
`developer` map naming the images, written from what actually exists on disk.

## Not answered yet

**How an unsigned image gets onto a developer key.** `halfkay_flash.py` in the
toolchain repo suggests HalfKay over a USB cable to the build host, rather than
the app doing it — but nothing in any repo here confirms which container format
that bootloader expects. Worth settling before relying on these in a test loop.
