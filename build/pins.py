"""
The release table, from node-onlykey-lib, for everything in build/.

    import pins
    t = pins.load()
    t["releases"]   {"latest": {...}, "v3.1.0": {...}, ... "v0.2-beta.8": {...}}
    t["lib"]        {"version": "0.3.0", "commit": "5104ee9..."}

build.py, make-docs.py and dryrun.py all read the table through here and
nowhere else, so there is one place that knows the table lives in a JS package.
build/versions.js explains why it does.

"LATEST" IS THIS BUILDER'S ROW, NOT THE LIB'S

The lib lists releases. "latest" is the working tree - the libraries and
OnlyKey-Firmware checkouts beside this repo, as they stand - and it is added
here, first, with blank pins. It is a ROLLING build: every sweep rebuilds it and
the new images replace the old under the same names. No history of working-tree
images is kept in the tree; git history has them.

That replaces the old "v3.0.5" row, which was the working tree under a version
number it never had: it was never released, the lib dropped it, and its images
sat on the page beside real releases looking like one.
"""

import glob
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

LATEST = "latest"


def node():
    """The node binary, found WITHOUT an interactive shell.

    A sweep runs inside a transient systemd --user unit (run-matrix.sh), where
    nvm has never been sourced and PATH is the manager's, not your login's. So:
    $NODE_BIN if you set one, then PATH, then /usr/local/bin/node, then the
    newest nvm install - rather than a bare "node" that works by hand and fails
    as a unit, which is how both of run-matrix.sh's documented traps presented.
    """
    for cand in (os.environ.get("NODE_BIN"), shutil.which("node"),
                 "/usr/local/bin/node"):
        if cand and os.path.exists(cand):
            return cand
    nvm = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))
    if nvm:
        return nvm[-1]
    raise RuntimeError("node not found - set NODE_BIN, or put node on PATH")


def load():
    r = subprocess.run([node(), os.path.join(HERE, "versions.js")],
                       capture_output=True, text=True, errors="replace", cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError("build/versions.js failed - has `npm install` been run "
                           "here?\n%s" % (r.stderr or "").strip())
    t = json.loads(r.stdout)          # json keeps the lib's order: newest first
    if LATEST in t["releases"]:
        raise RuntimeError("the lib has a release named %r, which is this "
                           "builder's working-tree row" % LATEST)
    t["releases"] = dict([(LATEST, {"libraries": "", "OnlyKey-Firmware": "",
                                    "unreleased": True, "compatibility": None})]
                         + list(t["releases"].items()))
    return t


def is_worktree(pin):
    """Blank pins = the working tree (see build.py's materialise_worktree)."""
    return not (pin.get("libraries") and pin.get("OnlyKey-Firmware"))


def _same(a, b):
    # Short and full shas both appear - the lib pins 7 characters, a record may
    # hold more - so compare on the shorter. Blank never matches.
    a, b = (a or "").lower(), (b or "").lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def built_at_pins(rec, pin):
    """Whether an index.json record IS this pin's build.

    A record used to count as built by NAME alone. Names do not change when a
    pin does - v3.1.0 has had three pins so far, as its release branch was
    re-squashed (eb25290, 16d8863, 8d28305) - so after a lib bump the sweep
    skipped v3.1.0 as "already built" and the page showed the new pins over the
    old images. Keyed on the recorded commits instead, a moved pin is simply
    not built yet.

    A working-tree row is never "built": it has no identifier to compare, and a
    stamp would claim a match and serve yesterday's source (the reasoning in
    materialise_worktree). It is always rebuilt.
    """
    if not rec or is_worktree(pin) or rec.get("worktree"):
        return False
    return (_same(rec.get("libraries"), pin["libraries"])
            and _same(rec.get("firmware"), pin["OnlyKey-Firmware"]))
