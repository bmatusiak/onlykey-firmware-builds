"""Dry-run the gates against every pin, without building anything.

Applies each variant's gates to the real onlykey.h at that pin and re-reads the
result, so a gate that silently failed to flip shows up as a wrong final state
rather than as a surprising .hex three hours later.
"""
import subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gates
import pins as pintable

# The workspace - the checkouts side by side - derived from where this file is,
# as build.py does. It was hard-coded to the Pi's /home/bmatusiak/projects/
# ok-firmware, so on any other machine this checked a directory that did not
# exist and reported every pin "unreadable".
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pins = pintable.load()["releases"]


def show(repo, sha, path):
    # A blank pin is the working tree ("latest"), answered from DISK, as
    # build.py's onlykey_h() does. `git show :path` would read the INDEX, which
    # is neither the tree nor any commit.
    if not sha:
        try:
            with open(os.path.join(ROOT, repo, path), encoding="utf-8",
                      errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    r = subprocess.run(["git", "-C", os.path.join(ROOT, repo), "show", "%s:%s" % (sha, path)],
                       capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


# STD_VERSION is always True: the IN TRVL edition cannot link on any pin. See
# FINDING-the-travel-edition-cannot-be-built-by-flipping-its-flag.md.
VARIANTS = [
    ("classic", "test", dict(debug=True,  std=True,  duo=False)),
    ("classic", "prod", dict(debug=False, std=True,  duo=False)),
    ("duo",     "test", dict(debug=True,  std=True,  duo=True)),
    ("duo",     "prod", dict(debug=False, std=True,  duo=True)),
]

ok = skipped = bad = 0
for rel, v in pins.items():
    h = show("libraries", v["libraries"], "onlykey/onlykey.h")
    if h is None:
        print("%-13s !! libraries pin %s unreadable" % (rel, v["libraries"]))
        bad += 1
        continue
    for model, build, want in VARIANTS:
        label = "%s %s/%s" % (rel, model, build)
        try:
            out, notes = gates.apply(h, **want)
        except gates.GateError as e:
            print("%-26s skip  %s" % (label, e))
            skipped += 1
            continue
        # Read the RESULT back rather than trusting the writer.
        got_debug = gates.read(out, "DEBUG")
        got_std = gates.read(out, "STD_VERSION")
        got_hwid = gates.read(out, "DEFINED_HWID")
        wrong = []
        if got_debug is not want["debug"]:
            wrong.append("DEBUG=%s" % got_debug)
        if got_std is not want["std"]:
            wrong.append("STD=%s" % got_std)
        if want["duo"] and got_hwid is not True:
            wrong.append("HWID=%s" % got_hwid)
        if not want["duo"] and got_hwid is True:
            wrong.append("HWID left ON")
        if wrong:
            print("%-26s BAD   %s" % (label, ", ".join(wrong)))
            bad += 1
        else:
            print("%-26s ok    %s" % (label, "; ".join(notes)))
            ok += 1

print()
print("%d buildable, %d not available at their pin, %d WRONG" % (ok, skipped, bad))
sys.exit(1 if bad else 0)
