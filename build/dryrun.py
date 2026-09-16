"""Dry-run the gates against every pin, without building anything.

Applies each variant's gates to the real onlykey.h at that pin and re-reads the
result, so a gate that silently failed to flip shows up as a wrong final state
rather than as a surprising .hex three hours later.
"""
import json, subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gates

ROOT = "/home/bmatusiak/projects/ok-firmware"
pins = json.load(open(ROOT + "/onlykey-firmware-builds/ok-versions.json"))


def show(repo, sha, path):
    r = subprocess.run(["git", "-C", ROOT + "/" + repo, "show", "%s:%s" % (sha, path)],
                       capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


VARIANTS = [
    ("classic", "test", dict(debug=True,  std=True,  duo=False)),
    ("classic", "prod", dict(debug=False, std=True,  duo=False)),
    ("duo",     "test", dict(debug=True,  std=True,  duo=True)),
    ("duo",     "prod", dict(debug=False, std=True,  duo=True)),
    ("trvl",    "test", dict(debug=True,  std=False, duo=False)),
    ("trvl",    "prod", dict(debug=False, std=False, duo=False)),
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
