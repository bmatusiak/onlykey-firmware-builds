#!/usr/bin/env python3
"""
Generate docs/data.json for the published page.

    python3 build/make-docs.py

WHY THE PAGE DOES NOT JUST READ THE REPO

GitHub Pages serving from /docs serves ONLY /docs. developer_firmware/ is not
reachable from the published site, so the page cannot fetch index.json or
matrix.json where they live. This flattens what the page needs into one file
inside docs/, and points downloads at github.com's raw URLs, which are served
whatever Pages is configured to do.

Run it after a sweep. It is seconds, reads four files and writes one, and it
never touches a build.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "developer_firmware")
DOCS = os.path.join(REPO, "docs")

# Where a browser can actually fetch a .hex from. Pages will not serve it, so
# these go to the repository itself. Derived from the remote so a fork publishes
# its own files rather than this one's.
def repo_slug():
    try:
        url = subprocess.run(["git", "-C", REPO, "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        url = ""
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" in url:
        return url.split("github.com")[-1].lstrip(":/")
    return "bmatusiak/onlykey-firmware-builds"


def branch():
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout.strip() or "main") if r.returncode == 0 else "main"


def load(path, fallback):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return fallback


def main():
    pins = load(os.path.join(REPO, "ok-versions.json"), {})
    index = load(os.path.join(OUT, "index.json"), {})
    matrix = load(os.path.join(OUT, "matrix.json"), [])
    failures = load(os.path.join(OUT, "failures.json"), {})

    slug, br = repo_slug(), branch()
    raw = "https://github.com/%s/raw/%s" % (slug, br)
    blob = "https://github.com/%s/blob/%s" % (slug, br)

    by_name = {m["name"]: m for m in matrix}

    releases = []
    for rel, pin in pins.items():
        variants = []
        for model in ("classic", "duo"):
            for build in ("test", "prod"):
                name = "%s-%s-%s" % (rel, model, build)
                m = by_name.get(name)
                rec = index.get(name)
                hexfile = os.path.join(OUT, name + ".hex")
                v = dict(name=name, model=model, build=build)
                if rec and os.path.exists(hexfile):
                    v.update(state="built",
                             bytes=rec.get("program_bytes"),
                             sha256=rec.get("sha256"),
                             seconds=rec.get("seconds"),
                             url="%s/developer_firmware/%s.hex" % (raw, name),
                             log="%s/developer_firmware/logs/%s.log" % (blob, name))
                elif name in failures:
                    v.update(state="failed", why=failures[name].get("why"),
                             log="%s/developer_firmware/logs/%s.log" % (blob, name))
                elif m and not m.get("available"):
                    v.update(state="unavailable", why=m.get("why"))
                else:
                    v.update(state="missing")
                variants.append(v)

        releases.append(dict(
            release=rel,
            libraries=pin.get("libraries"),
            firmware=pin.get("OnlyKey-Firmware"),
            signed=pin.get("file"),
            variants=variants,
        ))

    built = [v for r in releases for v in r["variants"] if v["state"] == "built"]
    data = dict(
        repo=slug,
        branch=br,
        releases=releases,
        totals=dict(
            releases=len(releases),
            built=len(built),
            buildable=sum(1 for m in matrix if m.get("available")),
            combinations=len(matrix),
            bytes=sum(v.get("bytes") or 0 for v in built),
            seconds=sum(v.get("seconds") or 0 for v in built),
        ),
    )

    os.makedirs(DOCS, exist_ok=True)
    path = os.path.join(DOCS, "data.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    print("wrote %s - %d releases, %d built of %d buildable"
          % (path, len(releases), data["totals"]["built"],
             data["totals"]["buildable"]))


if __name__ == "__main__":
    sys.exit(main())
