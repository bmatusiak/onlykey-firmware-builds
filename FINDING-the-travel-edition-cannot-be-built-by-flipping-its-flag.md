# The IN TRVL edition cannot be built by flipping its flag

**Not fixed. Not going to be.** The travel edition has been removed from the
build matrix, and this records why so nobody adds it back expecting it to work.

## What was tried

`onlykey.h` carries

    #define STD_VERSION //Define for STD edition firmare, undefine for IN TRVL edition firmware

which reads like a switch between two editions, and the emulator's `stage.js`
treats it as one — `OKEMU_STD=1` / `OKEMU_STD=0`. So the matrix builder offered
`std` and `trvl` as an axis alongside model and debug, and a sweep was started
that included it.

## What happened

`v3.0.4-classic-test-trvl`, after a full twenty-minute compile:

    /work/arduino/OnlyKey.ino:826: undefined reference to `okcrypto_sign'
    /work/arduino/OnlyKey.ino:830: undefined reference to `okcrypto_decrypt'
    /work/arduino/OnlyKey.ino:832: undefined reference to `okcrypto_hmacsha1'
    /work/arduino/OnlyKey.ino:865: undefined reference to `u2f_button'
    collect2: error: ld returned 1 exit status

Full log: `developer_firmware/logs/_travel-edition/`.

## Why

`STD_VERSION` guards the **definitions** of those functions in
`libraries/onlykey/okcrypto.cpp` and friends, but **not the calls** to them in
the sketch. With the flag off, the sketch still calls functions that no longer
exist, and the link fails.

Measured across every pin in `ok-versions.json` — `okcrypto_sign()` is inside an
`#ifdef STD_VERSION` block at v3.0.4, v3.0.3, v3.0.2, v3.0.1, v3.0.0, v2.1.2,
v2.1.1 and v2.1.0. (v0.2-beta.8 predates the function entirely.)

So this is not a v3.0.4 problem. The flag has never been a standalone switch on
any release we build.

## What that costs, and why it was removed rather than fixed

Linking is the **last** thing a build does, so every travel variant burns a
complete compile before failing. Twenty-eight travel variants at roughly twenty
minutes each is about nine hours of guaranteed failure per sweep.

Making it work would mean either guarding the call sites too, or providing stubs
— both changes to firmware behaviour, which is not this project's business. The
builder's rule is that a variant a pin cannot express is refused, not
approximated, and this is that rule applied one level up.

## What was NOT established

Whether the travel edition was ever built from these trees by some other route —
a different sketch, additional defines, or a build configuration that was never
committed. v2.1.1's pinned commit has `STD_VERSION` commented out, so *somebody*
had a travel configuration at that point; whether it compiled from that commit
alone was never tested, because by then the axis had been removed.

A static analysis was attempted to answer "which releases could ever link a
travel build" without spending a compile each. It reported that all nine link,
which contradicts the measured failure above, so it was wrong and was discarded
rather than debugged. If this question ever matters, build one and look.

## The state of things

`STD_VERSION` is now always ON in every build this project produces. The gate
still exists in `gates.py` and is still set explicitly — the flag is never
inherited from a pin — it is simply never set to off.
