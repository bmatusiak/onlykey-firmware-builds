#!/usr/bin/env python3
"""
Fetch trustcrypto's signed firmware releases, hash them, and check the hashes
against the ones trustcrypto published.

    python3 build/signed.py            fetch, hash, compare, write
    python3 build/signed.py --offline  re-hash what is on disk, no network

Writes signed_firmware/index.json and signed_firmware/<tag>/<file>.

WHERE A "PUBLISHED" HASH COMES FROM

trustcrypto publishes SHA-256 checksums in the BODY of each GitHub release, as
prose: a "SHA 256 checksums" heading, then a file name, then its hash on the
next line (or "name - hash" on one line). That is the only statement of what the
file should be - GitHub's own per-asset `digest` field exists only for assets
uploaded after mid-2025, and every OnlyKey asset is older, so it is null for all
of them today. It is recorded anyway, so a re-upload would be caught.

The body is hand-written and it shows: Beta 5's hashes have a space typed in
the middle, Beta 7 names "IN-TRVL" for an asset called "IN_TRVL", and one of
Beta 7's hashes is 63 characters long. So a file counts as VERIFIED only when
our hash of the downloaded bytes is EXACTLY one of the 64-character values in
its release's body (whitespace inside a value removed, nothing else). The label
next to it is recorded and checked, but it never makes a match on its own - a
name is not a hash. Everything else is reported as it is: a published value
that differs is a mismatch, and no value at all is "unpublished".

WHAT IS KEPT

Only the signed images (Signed_*.txt) are written into this repo: they are what
the page offers as a verified download, and what node-onlykey-lib's rows name.
The pre-signing betas shipped .cpp.hex images for the Teensy loader; those are
downloaded, hashed and compared the same way, but not kept - they are recorded
so the table is complete, and a signed production key will not take them.

WHY THE BYTES MUST SURVIVE GIT

These files are LF-only text. On a Windows checkout with core.autocrlf they
were being written back with CRLF (429300 bytes instead of 429285), so a hash
taken of the working copy did not match trustcrypto's - while the committed
blob did. signed_firmware/.gitattributes marks them -text, so every checkout is
byte-for-byte the file trustcrypto published, which is also what
raw.githubusercontent.com serves the page.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "signed_firmware")
UPSTREAM = "trustcrypto/OnlyKey-Firmware"
API = "https://api.github.com/repos/%s/releases?per_page=100" % UPSTREAM
FIRMWARE = re.compile(r"\.(txt|hex)$", re.I)


def get(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "onlykey-firmware-builds"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok and "api.github.com" in url:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def releases():
    """Every release, newest first. One page holds them all (17 in 2026)."""
    try:
        return json.loads(get(API))
    except Exception:
        # No network proxy for urllib, or rate-limited: gh carries its own auth.
        out = subprocess.run(["gh", "api", API.split("api.github.com/")[1],
                              "--paginate"], capture_output=True, text=True,
                             check=True).stdout
        return json.loads(out)


def norm(name):
    """A label as trustcrypto typed it, reduced so IN-TRVL == IN_TRVL."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def published(body):
    """(label, value, exact line) for every hash-looking line under the
    checksum heading. value is the hex with whitespace removed; its length is
    NOT checked here - a malformed value is reported, not dropped."""
    out, label, on = [], None, False
    for raw in (body or "").splitlines():
        line = raw.strip().strip("*`").strip()
        low = line.lower()
        if not on:
            on = ("sha" in low and "256" in low and
                  ("checksum" in low or "hash" in low))
            continue
        rest = line
        m = re.match(r"^(\S+\.(?:txt|hex))\s*(?:-\s*(.*))?$", line, re.I)
        if m:
            label, rest = m.group(1), (m.group(2) or "")
        hexed = re.sub(r"\s+", "", rest)
        if len(hexed) >= 32 and re.fullmatch(r"[0-9a-fA-F]+", hexed):
            out.append(dict(label=label, value=hexed.lower(), line=raw.strip()))
    return out


def variant(name):
    """What the file name says it is: Signed_OnlyKey_3_0_4_IN_TRVL.txt ->
    IN_TRVL. Descriptive only; the name is the identifier."""
    stem = re.sub(r"(\.cpp)?\.(txt|hex)$", "", name, flags=re.I)
    stem = re.sub(r"^(Signed_)?OnlyKey_", "", stem)
    stem = re.sub(r"^(\d+_\d+_\d+|Beta\d*)_?", "", stem)
    return stem or "STD"


def lib_version(tag):
    """The lib names releases without upstream's -prod suffix."""
    return tag[:-5] if tag.endswith("-prod") else tag


def lib_files():
    """{version: file} for every lib row that names a signed image."""
    r = subprocess.run(["node", "-e",
                        "const v=require('node-onlykey-lib/versions');const o={};"
                        "for(const r of v.list()){const p=v.pinsFor(r);"
                        "if(p&&p.file)o[r]=p.file}console.log(JSON.stringify(o))"],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError("node-onlykey-lib not installed? %s" % r.stderr.strip())
    return json.loads(r.stdout)


def check(sha, name, pubs, digest):
    """(match, published_sha256, source_line, note) - the rules in the header."""
    mine = [p for p in pubs if p["label"] and norm(p["label"]) == norm(name)]
    for p in pubs:
        if len(p["value"]) == 64 and p["value"] == sha:
            note = None
            if p["label"] and norm(p["label"]) != norm(name):
                note = "published under the label %r" % p["label"]
            if re.search(r"[0-9a-f]\s+[0-9a-f]", p["line"], re.I):
                note = (note + "; " if note else "") + \
                    "published with whitespace inside the value"
            if digest and digest != "sha256:" + sha:
                return False, p["value"], p["line"], "GitHub digest differs: " + digest
            return True, p["value"], p["line"], note
    if digest and digest != "sha256:" + sha:
        return False, None, None, "GitHub digest differs: " + digest
    if mine:
        p = mine[0]
        why = "published value differs from the file"
        if len(p["value"]) != 64:
            why = "published value is %d hex digits, not 64" % len(p["value"])
        return False, p["value"], p["line"], why
    if digest == "sha256:" + sha:
        return "unpublished", None, None, "no hash in the release body; GitHub digest matches"
    return "unpublished", None, None, "no hash for this file in the release body"


def main():
    offline = "--offline" in sys.argv
    # The API's answer, kept beside the scratch downloads (work/ is ignored):
    # --offline re-derives index.json from it without touching the network.
    cache = os.path.join(REPO, "work", "signed-releases.json")
    if offline:
        rels = json.load(open(cache))
    else:
        rels = releases()
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as fh:
            json.dump(rels, fh)
    lib = lib_files()
    tmp = os.path.join(REPO, "work", "signed-scratch")
    os.makedirs(tmp, exist_ok=True)

    out = []
    for r in rels:
        if r.get("draft"):
            continue
        tag = r["tag_name"]
        pubs = published(r.get("body"))
        files = []
        for a in r.get("assets", []):
            name = a["name"]
            if not FIRMWARE.search(name):
                continue
            signed = name.startswith("Signed_")
            # Everything lands in scratch first. Only a signed image whose hash
            # trustcrypto published is copied into signed_firmware/ - a file in
            # this repo reads as vouched for, and one that does not verify is
            # not, so it is recorded in index.json and kept out.
            scratch = os.path.join(tmp, tag, name)
            kept = os.path.join(OUT, tag, name)
            src = kept if (offline and os.path.exists(kept)) else scratch
            if not (offline and os.path.exists(src)):
                os.makedirs(os.path.dirname(scratch), exist_ok=True)
                with open(scratch, "wb") as fh:
                    fh.write(get(a["browser_download_url"], "application/octet-stream"))
                src = scratch
            data = open(src, "rb").read()
            if len(data) != a["size"]:
                raise RuntimeError("%s/%s: %d bytes, GitHub says %d"
                                   % (tag, name, len(data), a["size"]))
            sha = hashlib.sha256(data).hexdigest()
            match, pub, line, note = check(sha, name, pubs, a.get("digest"))
            f = dict(name=name, variant=variant(name), signed=signed,
                     size=a["size"], sha256=sha, published_sha256=pub,
                     github_digest=a.get("digest"), match=match,
                     source_line=line, note=note,
                     download_url=a["browser_download_url"])
            # OFFERED: the page hands the file over only when it is kept here
            # AND its hash is one trustcrypto published. Never on a mismatch,
            # never on "unpublished".
            f["offered"] = bool(signed and match is True)
            if f["offered"]:
                f["path"] = "signed_firmware/%s/%s" % (tag, name)
                if src != kept:
                    os.makedirs(os.path.dirname(kept), exist_ok=True)
                    with open(kept, "wb") as fh:
                        fh.write(data)
            else:
                if os.path.exists(kept):
                    os.remove(kept)
                f["why_not_offered"] = note if signed else (
                    "unsigned pre-signing image for the Teensy loader; "
                    "recorded for its hash only")
            files.append(f)
        out.append(dict(tag=tag, version=lib_version(tag), name=(r.get("name") or "").strip(),
                        date=(r.get("published_at") or "")[:10],
                        prerelease=bool(r.get("prerelease")),
                        url=r["html_url"],
                        lib_file=lib.get(lib_version(tag)),
                        published_hashes=len(pubs),
                        files=files))

    # CROSS-CHECK against node-onlykey-lib: every signed image a lib row names
    # must be here, verified; and every signed release here the lib has no
    # row naming is listed, so a gap in either is visible.
    by_ver = {r["version"]: r for r in out}
    cross = dict(lib_named_present=[], lib_named_missing=[], lib_named_unverified=[],
                 release_signed_not_named_by_lib=[])
    for ver, stem in lib.items():
        r = by_ver.get(ver)
        f = r and next((f for f in r["files"] if f["name"] == stem + ".txt"), None)
        if not f:
            cross["lib_named_missing"].append("%s: %s" % (ver, stem))
        elif f["match"] is not True:
            cross["lib_named_unverified"].append("%s: %s (%s)" % (ver, stem, f["match"]))
        else:
            cross["lib_named_present"].append("%s: %s" % (ver, stem))
    for r in out:
        named = lib.get(r["version"])
        for f in r["files"]:
            if f["signed"] and (not named or f["name"] != named + ".txt"):
                cross["release_signed_not_named_by_lib"].append(
                    "%s: %s" % (r["version"], f["name"]))

    doc = dict(
        note=("SHA-256 of every firmware asset on %s's GitHub releases, compared with "
              "the checksums trustcrypto published in each release's notes. match=true "
              "only when our hash of the downloaded file equals a 64-digit value "
              "published there. Made by build/signed.py." % UPSTREAM),
        made_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        upstream="https://github.com/%s/releases" % UPSTREAM,
        lib_crosscheck=cross,
        releases=out,
    )
    with open(os.path.join(OUT, "index.json"), "w", newline="\n") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")

    allf = [f for r in out for f in r["files"]]
    print("%d releases, %d firmware files: %d verified, %d mismatched, %d unpublished"
          % (len(out), len(allf), sum(f["match"] is True for f in allf),
             sum(f["match"] is False for f in allf),
             sum(f["match"] == "unpublished" for f in allf)))
    for r in out:
        for f in r["files"]:
            if f["match"] is not True:
                print("  %-12s %-44s %-11s %s" % (r["tag"], f["name"], f["match"], f["note"]))
    for k, v in cross.items():
        print("lib %s: %d%s" % (k, len(v), (" - " + ", ".join(v)) if v and k != "lib_named_present" else ""))


if __name__ == "__main__":
    sys.exit(main())
