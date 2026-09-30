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

The signed releases come from signed_firmware/index.json - run
build/signed.py first when trustcrypto publishes one.

Run it after a sweep. It is seconds - node-onlykey-lib's release table (via
build/pins.py) and three files in, one file out - and it
never touches a build.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pins as pintable

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
    """The branch the page's download links point at.

    The current branch only if origin has it: a link into a local-only branch
    is dead on the published page, and a feature branch is merged into main
    before Pages (which serves main:/docs) ever shows it. Otherwise main.
    $DOCS_BRANCH overrides both.
    """
    if os.environ.get("DOCS_BRANCH"):
        return os.environ["DOCS_BRANCH"]
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True)
    cur = r.stdout.strip() if r.returncode == 0 else ""
    if cur and subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q",
                               "refs/remotes/origin/" + cur],
                              capture_output=True).returncode == 0:
        return cur
    return "main"


def load(path, fallback):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return fallback


def signed_section(slug, br):
    """trustcrypto's signed releases, from signed_firmware/index.json (written
    by build/signed.py), with a URL the page can FETCH each kept file from.

    WHY raw.githubusercontent.com AND NOT A SAME-ORIGIN PATH: Pages serves only
    docs/, so signed_firmware/ is not on the site's origin; github.com release
    and /raw/ URLs answer a cross-origin fetch with no CORS header, so the page
    could not read the bytes to hash them. raw.githubusercontent.com sends
    Access-Control-Allow-Origin: *. Where the bytes come from does not decide
    anything - the page hashes them and compares with trustcrypto's published
    value, and hands the file over only on an exact match. `path` is kept too,
    so a server rooted at the repo (a local preview) can serve them itself.
    """
    idx = load(os.path.join(REPO, "signed_firmware", "index.json"), None)
    if not idx:
        return None
    raw = "https://raw.githubusercontent.com/%s/%s" % (slug, br)
    rels = []
    for r in idx["releases"]:
        files = []
        for f in r["files"]:
            g = {k: f.get(k) for k in ("name", "variant", "signed", "size", "sha256",
                                       "published_sha256", "match", "note",
                                       "source_line", "offered", "path",
                                       "download_url", "why_not_offered")}
            g["url"] = "%s/%s" % (raw, f["path"]) if f.get("path") else None
            files.append(g)
        rels.append(dict(tag=r["tag"], version=r["version"], name=r["name"],
                         date=r["date"], prerelease=r["prerelease"], url=r["url"],
                         lib_file=r.get("lib_file"), files=files))
    return dict(made_at=idx.get("made_at"), upstream=idx.get("upstream"),
                note=idx.get("note"), lib_crosscheck=idx.get("lib_crosscheck"),
                releases=rels)


def main():
    # Not load(..., {}) like the files below: an empty table would publish an
    # empty page. A broken install should stop here, loudly.
    table = pintable.load()
    pins = table["releases"]
    index = load(os.path.join(OUT, "index.json"), {})
    matrix = load(os.path.join(OUT, "matrix.json"), [])
    failures = load(os.path.join(OUT, "failures.json"), {})

    slug, br = repo_slug(), branch()
    raw = "https://github.com/%s/raw/%s" % (slug, br)
    blob = "https://github.com/%s/blob/%s" % (slug, br)

    by_name = {m["name"]: m for m in matrix}
    signed = signed_section(slug, br)

    releases = []
    signed_below = False
    for rel, pin in pins.items():
        worktree = pintable.is_worktree(pin)
        variants = []
        for model in ("classic", "duo"):
            for build in ("test", "prod"):
                name = "%s-%s-%s" % (rel, model, build)
                m = by_name.get(name)
                rec = index.get(name)
                hexfile = os.path.join(OUT, name + ".hex")
                v = dict(name=name, model=model, build=build)
                # An image counts as this release's only if it was built at
                # the release's CURRENT pins - the same rule build.py skips by
                # (pins.built_at_pins). After a lib bump the old image is
                # still on disk under the same name, and listing it as
                # "built" would put the new pins over the old binary.
                current = rec and (rec.get("worktree") if worktree
                                   else pintable.built_at_pins(rec, pin))
                if rec and os.path.exists(hexfile) and not current:
                    v.update(state="stale",
                             firmware=rec.get("firmware"),
                             libraries=rec.get("libraries"),
                             built_at=rec.get("built_at"),
                             sha256=rec.get("sha256"),
                             url="%s/developer_firmware/%s.hex" % (raw, name),
                             log="%s/developer_firmware/logs/%s.log" % (blob, name))
                elif rec and os.path.exists(hexfile):
                    v.update(state="built",
                             firmware=rec.get("firmware"),
                             libraries=rec.get("libraries"),
                             built_at=rec.get("built_at"),
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

        # A working-tree release has blank pins, so its commits come from what
        # its images were actually built from - the newest built variant.
        built_here = sorted((v for v in variants if v.get("state") == "built"),
                            key=lambda v: v.get("built_at") or 0)
        newest = built_here[-1] if built_here else {}

        # PRE-RELEASE: pinned, but newer than every release that has a signed
        # image. The lib marks v3.1.0 `unreleased: false` - it is treated as
        # the release because it is what ships next - so that flag cannot say
        # it; and "no signed file" alone would also catch v3.0.0, which shipped
        # but has no image in signed_firmware/. Nothing newer than the last
        # signed release is released until it is signed, and the label goes
        # away by itself when the lib's row gains its `file`.
        if pin.get("file"):
            signed_below = True
        prerelease = not worktree and not signed_below

        releases.append(dict(
            release=rel,
            libraries=pin.get("libraries") or newest.get("libraries") or "",
            firmware=pin.get("OnlyKey-Firmware") or newest.get("firmware") or "",
            signed=pin.get("file"),
            worktree=worktree,
            prerelease=prerelease,
            built_at=newest.get("built_at") if worktree else None,
            # The lib's MODEL of what a signed build of this release reports
            # (compatibilityOf(v).capabilities) - not probed from the .hex. A
            # working-tree row has none: the lib has no row for it.
            compatibility=pin.get("compatibility"),
            variants=variants,
        ))

    built = [v for r in releases for v in r["variants"] if v["state"] == "built"]
    data = dict(
        repo=slug,
        branch=br,
        # Which release table this page was built from, so a reader can tell
        # - the lib is pinned by commit, and its version moves far less often.
        lib=table["lib"],
        releases=releases,
        signed=signed,
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
    print("wrote %s - %d releases, %d built of %d buildable, %d signed files offered"
          % (path, len(releases), data["totals"]["built"],
             data["totals"]["buildable"],
             sum(f["offered"] for r in (signed or {}).get("releases", [])
                 for f in r["files"])))


if __name__ == "__main__":
    sys.exit(main())
