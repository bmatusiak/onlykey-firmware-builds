#!/usr/bin/env python3
"""
Build every OnlyKey firmware variant from the pins in ok-versions.json.

    python3 build.py --list                      what would be built, nothing else
    python3 build.py                             the whole matrix
    python3 build.py --only v3.0.4               one release, every variant
    python3 build.py --only v3.0.4 --models duo --builds prod

WHAT A VARIANT IS

Three independent switches, all set explicitly and never inherited from the pin
(gates.py explains at length why inheriting is wrong here):

    build    test / prod      the DEBUG gate, which also decides whether the
                              firmware reports itself as -test or -prod
    model    classic / duo    the DEFINED_HWID override
    (STD_VERSION is always on - the travel edition cannot link on any pin;
     see FINDING-the-travel-edition-cannot-be-built-by-flipping-its-flag.md)

A variant that a pin cannot express is REFUSED, not approximated: there is no
DUO build of v2.1.0 because the DUO postdates it, and emitting a classic image
under a filename saying DUO would be worse than emitting nothing.

WHY THE TOOLCHAIN IS COPIED ONCE, NOT PER BUILD

The stock in-docker-build.sh begins each run by deleting and re-copying the
unpacked Arduino tree - 876 MB. That is defensible for one build and absurd for
forty-six; on an emulated Pi it would add hours of pure file copying. So the
pristine tree is unpacked once into work/arduino-pristine and never touched
again, and each build starts by rsyncing it into place. rsync only moves what a
previous variant actually changed, which is the libraries directory and a few
core headers.

WHAT THE CONTAINER IS FOR

Only the compile. All the git work and every source edit happens out here on the
host, where they can be inspected and where a failure is legible. The container
sees a prepared tree and runs one command in it. That is a deliberate departure
from in-docker-build.sh, which does its git and its copying inside, where a
mistake surfaces as a compiler error twenty minutes later.
"""

import argparse
import hashlib
import json
import os
import platform
import pwd
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gates

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                 # onlykey-firmware-builds/
ROOT = os.path.dirname(REPO)                 # the workspace, checkouts side by side

WORK = os.path.join(REPO, "work")
# The built firmware lives IN THE REPO, beside signed_firmware/, because that is
# what it is for: an unsigned image a developer key will accept, published the
# same way the signed releases are. work/ stays ignored - it is 876 MB of
# reproducible scratch - but the artefacts and their logs are the product.
OUT = os.path.join(REPO, "developer_firmware")
IMAGE = "onlykey/onlykey-firmware-toolchain"

# Set from --keep-objects. This exists to TEST whether the object wipe is
# needed, not as a convenience. Builds are deterministic apart from a TIME_T
# stamp Teensyduino bakes in, so a dirty build that matches a clean one
# everywhere else is evidence the wipe can go - and it costs roughly eight
# hours across a full matrix, so that is worth knowing rather than assuming.
KEEP_OBJECTS = False

TOOLCHAIN = os.path.join(ROOT, "arduino-1.6.5-r5-teensy_127")
FIRMWARE_REPO = os.path.join(ROOT, "OnlyKey-Firmware")
LIBRARIES_REPO = os.path.join(ROOT, "libraries")

# Where in-docker-build.sh puts the firmware's own core sources. The firmware
# ships .c/.h files that REPLACE parts of the teensy3 core - that is how it gets
# its USB descriptors and its keylayouts.
CORE = os.path.join("hardware", "teensy", "avr", "cores", "teensy3")


STATUS = os.path.join(WORK, "status.json")
LOGS = os.path.join(OUT, "logs")

# Every build keeps its own log, named for the variant, and they are never
# overwritten. A single current.log answered "what is happening now" and
# nothing else: the moment a build failed and the next one started, the only
# record of WHY it failed was gone. On a sweep of fifty-odd builds run
# overnight, that is the one thing you actually need afterwards.
def log_for(name):
    os.makedirs(LOGS, exist_ok=True)
    return os.path.join(LOGS, name + ".log")

# Everything the status server needs, rewritten whenever it changes. A plain
# file rather than a socket or a port: the build is the thing that must not
# break, and a watcher that has crashed, or was never started, must not be able
# to affect it. Anyone can read it, including `cat`.
_state = {}


def publish(**changes):
    """Update the status file. Never fatal - a failure to report progress is not
    a reason to lose a twenty-minute build."""
    _state.update(changes)
    _state["updated"] = time.time()
    try:
        os.makedirs(WORK, exist_ok=True)
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, STATUS)          # atomic, so a reader never sees half
    except OSError:
        pass


def run(cmd, log=None, **kw):
    """Foreground. Output inherited unless a log is given.

    A build tool that hides its output is a build tool nobody can diagnose - so
    the driver's own progress always goes to the console. The compiler's
    thousands of lines go to a log instead, where the status page can tail them
    without drowning the terminal.
    """
    if log is None:
        return subprocess.run(cmd, check=True, **kw)
    with open(log, "wb") as f:
        return subprocess.run(cmd, check=True, stdout=f,
                              stderr=subprocess.STDOUT, **kw)


def git(repo, *args, capture=True):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=capture, text=True, errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s"
                           % (" ".join(args), repo, (r.stderr or "").strip()))
    return r.stdout


def materialise(repo, sha, dest):
    """Unpack one commit into dest, without touching the checkout.

    `git archive` rather than a worktree or a clone: HEAD never moves, the
    checkout is never written to, and nothing is left behind to go stale. The
    same reasoning as stage.js's materialise(), which uses ls-tree/cat-file to
    the same end.

    Cached by a stamp file, because forty-six builds share nine sets of sources.
    """
    stamp = dest + ".commit"
    if os.path.isdir(dest) and os.path.exists(stamp):
        if open(stamp).read().strip() == sha:
            return
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    tar = subprocess.Popen(["git", "-C", repo, "archive", sha],
                           stdout=subprocess.PIPE)
    ok = subprocess.run(["tar", "-x", "-C", dest], stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait() != 0 or ok.returncode != 0:
        raise RuntimeError("could not materialise %s from %s" % (sha, repo))
    open(stamp, "w").write(sha)


def find_sketch(fw_dir):
    """The .ino to compile, FOUND rather than hardcoded.

    in-docker-build.sh hardcodes OnlyKey/OnlyKey.ino, which is right for eight
    of the nine pins and wrong for v0.2-beta.8, whose sketch is
    OnlyKey_Beta/OnlyKey_Beta.ino. Looking for it costs nothing and does not
    need a table that goes stale.
    """
    found = []
    for entry in sorted(os.listdir(fw_dir)):
        d = os.path.join(fw_dir, entry)
        if not os.path.isdir(d):
            continue
        ino = os.path.join(d, entry + ".ino")
        if os.path.exists(ino):
            found.append(ino)
    if not found:
        raise RuntimeError("no <dir>/<dir>.ino sketch found in %s" % fw_dir)
    # OnlyKey proper wins if several exist; the repo also carries example sketches.
    for f in found:
        if os.path.basename(f).startswith("OnlyKey"):
            return f
    return found[0]


def prepare_pristine():
    """Unpack the toolchain once. 876 MB, so this is worth not repeating."""
    pristine = os.path.join(WORK, "arduino-pristine")
    src = os.path.join(TOOLCHAIN, "arduino-1.6.5-r5")
    if os.path.isdir(os.path.join(pristine, "hardware")):
        return pristine
    if not os.path.isdir(src):
        raise RuntimeError("toolchain not found at %s - is the checkout there?" % src)
    print("== unpacking the pristine toolchain (876 MB, once)")
    shutil.rmtree(pristine, ignore_errors=True)
    run(["cp", "-a", src, pristine])
    return pristine


def stage(pristine, fw_dir, lib_dir, release, debug, std, duo):
    """Build one Arduino tree with this variant's sources and flags.

    Returns (arduino_dir, notes). Raises gates.GateError if the variant is not
    available at this pin.
    """
    arduino = os.path.join(WORK, "arduino")

    # --delete so a previous variant's libraries and core edits are undone.
    # Without it, v2.1.0's libraries would linger into a v3.0.4 build and the
    # result would be neither release.
    run(["rsync", "-a", "--delete", pristine + "/", arduino + "/"])

    # THE OBJECT TREE, WHICH IS NOT INSIDE work/arduino.
    #
    # preferences.txt sets build.path=../build, so Arduino's objects land in
    # work/build - a sibling the rsync above never touches.
    #
    # Reuse WITHIN a release, wipe when the release CHANGES. Both halves are
    # measured, and the first conclusion drawn here was wrong, so the evidence
    # is worth writing down:
    #
    #   * Reuse is safe and it is where the time is. v3.0.4 prod, built on
    #     v3.0.4 test's objects, came out byte-identical to a clean build of
    #     the same variant - and took 630s against 1270s. The suspicion that
    #     it had reused stale objects was WRONG: the only difference between
    #     any two builds is a TIME_T word at 0x37C that Teensyduino stamps in
    #     to seed the RTC, so no build of anything is ever byte-reproducible.
    #
    #   * Across releases the reuse buys nothing anyway. v2.1.0 built on
    #     v3.0.4's objects was also byte-identical to a clean v2.1.0 - Arduino
    #     did rebuild what mattered - but it had to redo the work regardless,
    #     because the firmware's own .c/.h files overwrite the teensy3 core and
    #     every one of them changed.
    #
    # So the wipe happens exactly where it is free, and the reuse happens
    # exactly where it halves a twenty-minute build. Across the whole matrix
    # that is something like eight hours.
    #
    # --keep-objects disables even the per-release wipe. It exists for
    # reproducing the measurements above, not for ordinary use.
    build_dir = os.path.join(WORK, "build")
    stamp = os.path.join(WORK, "build.release")
    previous = None
    if os.path.exists(stamp):
        previous = open(stamp).read().strip()
    if previous != release and not KEEP_OBJECTS:
        shutil.rmtree(build_dir, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    with open(stamp, "w") as f:
        f.write(release)

    core = os.path.join(arduino, CORE)

    # The firmware's own core sources replace parts of teensy3 - usb_desc.h,
    # usb_dev.h, usb_rawhid.h, keylayouts.h and the .c files beside them.
    for name in sorted(os.listdir(fw_dir)):
        if name.endswith(".c") or name.endswith(".h"):
            shutil.copy2(os.path.join(fw_dir, name), core)

    # keylayouts.h has its own copy of the DEBUG decision and asks to be kept in
    # sync by hand. Done here, after the copy, so it is the firmware's file that
    # gets gated rather than the stock teensy one.
    kl = os.path.join(core, "keylayouts.h")
    layouts = "no keylayouts.h at this pin"
    if os.path.exists(kl):
        text, layouts = gates.sync_keylayouts(open(kl, encoding="utf-8",
                                                   errors="replace").read(), debug)
        open(kl, "w", encoding="utf-8").write(text)

    # The pinned libraries, over the stock ones.
    libdest = os.path.join(arduino, "libraries")
    for name in sorted(os.listdir(lib_dir)):
        s = os.path.join(lib_dir, name)
        d = os.path.join(libdest, name)
        if os.path.isdir(s):
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    okh = os.path.join(libdest, "onlykey", "onlykey.h")
    if not os.path.exists(okh):
        raise RuntimeError("libraries/onlykey/onlykey.h missing at this pin")
    text, notes = gates.apply(
        open(okh, encoding="utf-8", errors="replace").read(),
        debug=debug, std=std, duo=duo)
    open(okh, "w", encoding="utf-8").write(text)

    # Read the flags back off disk. The gate module already verifies its own
    # edit, but this is the file the compiler will actually open.
    final = open(okh, encoding="utf-8", errors="replace").read()
    if gates.read(final, "DEBUG") is not debug:
        raise RuntimeError("DEBUG did not stick in the staged onlykey.h")
    if gates.read(final, "STD_VERSION") is not std:
        raise RuntimeError("STD_VERSION did not stick in the staged onlykey.h")

    return arduino, notes + [layouts]


def login_gid():
    """The user's REAL primary group, from the passwd file.

    Not os.getgid(). run-matrix.sh launches the sweep through `sg docker` so
    that a systemd --user unit can reach the docker socket, and sg makes docker
    the PRIMARY group - so os.getgid() returns 105, the docker gid, rather than
    1000. Passing that to `docker run -u` gives the container a user who cannot
    write to a build tree owned by bmatusiak:bmatusiak, and every build fails
    with a bare exit 1 from the Arduino IDE.

    MEASURED: `-u 1000:105` under the sweep, `-u 1000:1000` by hand - which is
    exactly why it worked interactively and failed as a unit, for the second
    time and for a completely different reason than the first.

    pwd.getpwuid() reads the passwd entry, which sg does not change.
    """
    return pwd.getpwuid(os.getuid()).pw_gid


def compile_in_docker(arduino, sketch, log_path):
    """One command in the container: the Arduino IDE, headless.

    build.path is ../build in preferences.txt, so the objects and the .hex land
    in work/build beside the tree.
    """
    plat = [] if platform.machine() == "x86_64" else ["--platform", "linux/amd64"]

    # --ulimit nofile: docker hands a container 1073741816 where this host's
    # shell gets 1024. The IDE is Java, the JVM sizes its descriptor table from
    # that limit, and aborts with "unable to allocate file descriptor table -
    # out of memory" before loading a file. See arduino-1.6.5-r5-teensy_127/
    # pi-build.sh, where this cost an hour to find.
    # --init: XVFB-RUN MUST NOT BE PID 1.
    #
    # xvfb-run starts Xvfb in the background and waits for it. As PID 1 that
    # wait never returns - PID 1 has no job control and different child-reaping
    # duties, and xvfb-run was not written to be it. MEASURED TWICE: the
    # container sat with exactly two processes, xvfb-run and Xvfb, no java at
    # all and not one line of output, for twenty minutes.
    #
    # The stock in-docker-build.sh never hit this because IT was PID 1 and
    # xvfb-run was merely its child.
    #
    # Wrapping in `bash -c "cd X && xvfb-run ..."` DOES NOT FIX IT, which cost
    # a second twenty minutes to learn: bash exec-optimizes the last command of
    # a && chain, replacing itself instead of forking, so PID 1 goes straight
    # back to xvfb-run. A probe that piped to `head` appeared to fix it only
    # because a pipeline defeats that optimization.
    #
    # --init puts tini at PID 1 and everything else below it, which is true
    # whatever the shell decides to do. The image already ships tini.
    inner = ("cd /work/arduino && xvfb-run -- ./arduino --verify %s "
             "--preferences-file ./preferences.txt -v"
             % sketch.replace(WORK, "/work"))

    cmd = ["docker", "run", "--rm", "--init"] + plat + [
        "--ulimit", "nofile=1024:1024",
        "-v", "%s:/work" % WORK,
        "-u", "%d:%d" % (os.getuid(), login_gid()),
        IMAGE,
        "bash", "-c", inner,
    ]
    run(cmd, log=log_path)


def collect(name):
    """Take the .hex, and check it is one.

    The Arduino IDE will exit 0 having produced nothing, so the artefact is
    checked rather than the exit code - and checked for being a complete Intel
    HEX, not merely for existing.
    """
    build = os.path.join(WORK, "build")
    hexes = [f for f in os.listdir(build) if f.endswith(".hex")] \
        if os.path.isdir(build) else []
    if len(hexes) != 1:
        raise RuntimeError("expected one .hex in work/build, found %d" % len(hexes))

    src = os.path.join(build, hexes[0])
    data = open(src, "rb").read()
    lines = [l for l in data.decode("ascii", "replace").splitlines() if l.strip()]
    if not lines or not lines[0].startswith(":"):
        raise RuntimeError("%s is not Intel HEX" % hexes[0])
    if lines[-1].strip() != ":00000001FF":
        raise RuntimeError("%s has no Intel HEX end-of-file record - truncated?"
                           % hexes[0])
    prog = 0
    for l in lines:
        b = bytes.fromhex(l[1:])
        if (sum(b) & 0xFF) != 0:
            raise RuntimeError("%s has a bad checksum on: %s" % (hexes[0], l[:20]))
        if b[3] == 0:
            prog += b[0]

    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, name + ".hex")
    shutil.copy2(src, dest)
    return dest, prog, hashlib.sha256(data).hexdigest()


MODELS = ("classic", "duo")
BUILDS = ("test", "prod")

# STD_VERSION is always ON. It was briefly an axis - the header calls it a
# switch between the standard and IN TRVL editions, and stage.js treats it as
# one - but a travel build cannot LINK on any pin we have: the flag guards the
# definitions of okcrypto_sign, okcrypto_decrypt, okcrypto_hmacsha1 and
# u2f_button while the sketch still calls them. Linking is the last step, so
# each attempt costs a full twenty-minute compile before failing.
# See FINDING-the-travel-edition-cannot-be-built-by-flipping-its-flag.md.
#
# The gate is still SET explicitly on every build rather than inherited from
# the pin - v2.1.1 ships with it off - it is simply never set to off.
STD = True

MATRIX = os.path.join(OUT, "matrix.json")
INDEX = os.path.join(OUT, "index.json")


def variant_name(rel, model, build):
    return "%s-%s-%s" % (rel, model, build)


def survey():
    """The WHOLE matrix and which of it can exist, without building anything.

    Applying the gates to each pin's onlykey.h takes seconds and answers, for
    every combination, whether that firmware is buildable at all - there is no
    DUO of v2.1.0 because the DUO postdates it. Written out so the status page
    can show the full picture from the first second, rather than only the
    handful of variants the current invocation happens to cover.
    """
    pins = json.load(open(os.path.join(REPO, "ok-versions.json")))
    rows = []
    for rel, v in pins.items():
        try:
            h = git(LIBRARIES_REPO, "show", "%s:onlykey/onlykey.h" % v["libraries"])
            err = None
        except RuntimeError as e:
            h, err = None, str(e)
        for model in MODELS:
            for build in BUILDS:
                row = dict(name=variant_name(rel, model, build),
                           release=rel, model=model, build=build)
                if h is None:
                    row.update(available=False, why=err)
                else:
                    try:
                        gates.apply(h, debug=(build == "test"), std=STD,
                                    duo=(model == "duo"))
                        row.update(available=True)
                    except gates.GateError as ex:
                        row.update(available=False, why=str(ex))
                rows.append(row)
    return rows


def write_survey():
    rows = survey()
    os.makedirs(OUT, exist_ok=True)
    tmp = MATRIX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, MATRIX)
    return rows


FAILURES = os.path.join(OUT, "failures.json")


def record_failure(name, why):
    """Durable record of a failed build, so the page still shows it red after a
    restart - and so the reason survives the sweep that produced it.

    Kept apart from index.json on purpose: the resume logic skips what is in
    the index, and a failure must NOT be skipped. A failed variant is retried
    on the next sweep, which is what you want after fixing whatever broke it.
    """
    try:
        d = {}
        try:
            d = json.load(open(FAILURES))
        except (OSError, ValueError):
            pass
        d[name] = {"why": str(why), "at": time.time()}
        os.makedirs(OUT, exist_ok=True)
        tmp = FAILURES + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, FAILURES)
    except OSError:
        pass


def clear_failure(name):
    """A build that succeeds is no longer a failure. Without this a variant
    fixed on the second attempt would stay red for ever."""
    try:
        d = json.load(open(FAILURES))
    except (OSError, ValueError):
        return
    if d.pop(name, None) is not None:
        try:
            tmp = FAILURES + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f, indent=1)
            os.replace(tmp, FAILURES)
        except OSError:
            pass


PINS = os.path.join(REPO, "ok-versions.json")


def update_pins():
    """Name each release's developer builds in ok-versions.json.

    The file already pairs a release with the SIGNED image that shipped for it:

        "v3.0.4": { "libraries": ..., "OnlyKey-Firmware": ...,
                    "file": "Signed_OnlyKey_3_0_4_STD" }

    A developer key refuses that file and takes only an unsigned build, so the
    manifest should name those too - otherwise the images in developer_firmware/
    are a directory listing you have to interpret, rather than something a tool
    can look up. `developer` is a map because a release has several: classic and
    DUO, test and production.

    Written from index.json, so it only ever names files that exist and were
    actually built. Rebuilt in full each time rather than appended to, so a
    variant whose .hex has been deleted stops being advertised.

    NOTE: this is this repo's copy of ok-versions.json. ok-rn has its own, and
    the two will now differ by this field. They agree on the pins, which is what
    both actually read.
    """
    try:
        pins = json.load(open(PINS))
        idx = json.load(open(INDEX))
    except (OSError, ValueError):
        return

    by_release = {}
    for name, r in idx.items():
        if not os.path.exists(os.path.join(OUT, name + ".hex")):
            continue
        key = "%s-%s" % (r.get("model"), r.get("build"))
        by_release.setdefault(r["release"], {})[key] = name + ".hex"

    changed = False
    for rel, entry in pins.items():
        want = dict(sorted(by_release.get(rel, {}).items()))
        if want != entry.get("developer", {}):
            if want:
                entry["developer"] = want
            else:
                entry.pop("developer", None)
            changed = True

    if not changed:
        return
    try:
        tmp = PINS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pins, f, indent=1)
            f.write("\n")
        os.replace(tmp, PINS)
    except OSError:
        pass


def record(results):
    """Merge this run's results into the CUMULATIVE index, keyed by variant.

    manifest.json was rewritten per invocation, so a run of one variant erased
    the record of the other forty-five. The point of a matrix is the whole of
    it, built up over however many sessions it takes, so the durable record has
    to accumulate rather than replace.
    """
    idx = {}
    try:
        idx = json.load(open(INDEX))
    except (OSError, ValueError):
        pass
    for r in results:
        r = dict(r)
        r["built_at"] = time.time()
        idx[r["name"]] = r
    os.makedirs(OUT, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f, indent=1)
    os.replace(tmp, INDEX)
    return idx


def main():
    # Line-buffered even when piped. Python's default block buffering makes a
    # long run look like a hang: a matrix sweep is hours, and the first check
    # of its log showed zero bytes while the build was perfectly alive. A tool
    # you cannot watch is a tool you cannot diagnose.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated releases; default all")
    ap.add_argument("--models", default="classic,duo")
    ap.add_argument("--builds", default="test,prod")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild variants already recorded in out/index.json")
    ap.add_argument("--keep-objects", action="store_true",
                    help="do not wipe work/build between variants (test only)")
    ap.add_argument("--survey", action="store_true",
                    help="write out/matrix.json - the whole matrix and what of "
                         "it is buildable - then stop. Seconds, no compiling.")
    ap.add_argument("--list", action="store_true",
                    help="say what would be built and stop")
    args = ap.parse_args()

    global KEEP_OBJECTS
    KEEP_OBJECTS = args.keep_objects

    pins = json.load(open(os.path.join(REPO, "ok-versions.json")))
    releases = args.only.split(",") if args.only else list(pins)
    for r in releases:
        if r not in pins:
            sys.exit("no such release in ok-versions.json: %s (have: %s)"
                     % (r, ", ".join(pins)))

    models = args.models.split(",")
    builds = args.builds.split(",")

    # Everything the axes allow. Which combinations actually EXIST is decided
    # per pin by the gates, not here - asking for a DUO of v2.1.0 gets a refusal
    # naming the reason, which is more useful than silently never trying.
    #
    # This deliberately matches what survey() reports as buildable, so the
    # status page's "built N of M" can reach M.
    plan = [(rel, m, b) for rel in releases for m in models for b in builds]

    if args.list:
        for rel, m, b in plan:
            print(variant_name(rel, m, b))
        print("\n%d variants" % len(plan))
        return

    if args.survey:
        rows = write_survey()
        avail = sum(1 for r in rows if r["available"])
        print("%d combinations, %d buildable, %d not available at their pin"
              % (len(rows), avail, len(rows) - avail))
        print("written to %s" % MATRIX)
        return

    os.makedirs(WORK, exist_ok=True)

    # Refresh the full-matrix survey on every run, so the status page shows all
    # of it and not merely the slice this invocation covers. Seconds, not
    # minutes - it reads nine headers and applies the gates in memory.
    try:
        write_survey()
    except Exception as e:                           # noqa: BLE001
        print("(could not write the matrix survey: %s)" % e)

    names = [variant_name(rel, m, b) for rel, m, b in plan]
    started = time.time()
    publish(started=started, total=len(plan), done=0,
            plan=[{"name": n, "state": "pending"} for n in names],
            current=None, results=[], failures=[], skips=[], finished=None)

    pristine = prepare_pristine()

    built_already = {}
    if not args.rebuild:
        try:
            built_already = json.load(open(INDEX))
        except (OSError, ValueError):
            pass

    results, failures, skips = [], [], []

    for i, (rel, model, build) in enumerate(plan, 1):
        name = names[i - 1]
        print("\n=== [%d/%d] %s" % (i, len(plan), name))
        v = pins[rel]
        t0 = time.time()

        # Already built, so skip it. A full sweep is many hours and WILL be
        # interrupted - a reboot, a killed session, a change of mind. Restarting
        # it should cost the builds that are missing, not the ones that are
        # done. --rebuild forces the work anyway.
        # The INDEX and the ARTEFACT must both be there. The index is what makes
        # this repo portable - clone it on another machine, or add a pin, and a
        # sweep builds only what is missing rather than starting from nothing -
        # but an index entry whose .hex has been deleted would silently skip and
        # leave a hole nobody could see. Checking the file costs a stat.
        if (name in built_already and not args.rebuild
                and os.path.exists(os.path.join(OUT, name + ".hex"))):
            print("    already built (%s) - skipping; --rebuild to force"
                  % built_already[name].get("sha256", "")[:12])
            _state["plan"][i - 1].update(state="done",
                                         seconds=built_already[name].get("seconds"))
            publish()
            continue

        _state["plan"][i - 1]["state"] = "building"
        publish(current={"name": name, "index": i, "started": t0,
                         "release": rel, "model": model,
                         "build": build})
        try:
            fw = os.path.join(WORK, "src", rel, "OnlyKey-Firmware")
            lib = os.path.join(WORK, "src", rel, "libraries")
            materialise(FIRMWARE_REPO, v["OnlyKey-Firmware"], fw)
            materialise(LIBRARIES_REPO, v["libraries"], lib)

            arduino, notes = stage(pristine, fw, lib, rel,
                                   debug=(build == "test"),
                                   std=STD,
                                   duo=(model == "duo"))
            print("    %s" % "; ".join(notes))

            sketch = find_sketch(fw)
            print("    sketch %s" % os.path.relpath(sketch, fw))

            compile_in_docker(arduino, sketch, log_for(name))
            dest, prog, digest = collect(name)
            took = int(time.time() - t0)
            print("    -> %s  %d bytes program  %s  (%ds)"
                  % (os.path.basename(dest), prog, digest[:12], took))
            results.append(dict(name=name, release=rel, model=model,
                                build=build,
                                firmware=v["OnlyKey-Firmware"],
                                libraries=v["libraries"],
                                program_bytes=prog, sha256=digest, seconds=took))
            _state["plan"][i - 1].update(state="done", seconds=took,
                                         program_bytes=prog,
                                         sha256=digest[:12])
            clear_failure(name)
        except gates.GateError as e:
            print("    skipped: %s" % e)
            skips.append((name, str(e)))
            _state["plan"][i - 1].update(state="skipped", why=str(e))
        except Exception as e:                       # noqa: BLE001
            print("    FAILED: %s" % e)
            failures.append((name, str(e)))
            _state["plan"][i - 1].update(state="failed", why=str(e))
            record_failure(name, e)

        # Recorded after EVERY variant, not at the end. A sweep is many hours
        # and anything can interrupt it - a reboot, a killed session, a power
        # cut. Forty-one builds that were never written down would be forty-one
        # builds to do again.
        if results:
            record(results[-1:])
            update_pins()
        publish(done=len(results), results=results,
                failures=failures, skips=skips, current=None)

    if results:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, "manifest.json"), "w") as f:
            json.dump(results, f, indent=2)

    publish(finished=time.time())

    print("\n=== %d built, %d unavailable, %d failed, %dm total"
          % (len(results), len(skips), len(failures),
             int((time.time() - started) / 60)))
    for n, why in failures:
        print("  FAILED %s: %s" % (n, why))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
