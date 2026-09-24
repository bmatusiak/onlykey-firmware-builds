#!/usr/bin/env bash
#
# Follow 0c-coder's master into the v3.0.5 images and the published page.
#
#     npm run update             fetch his master; if it moved, rebuild v3.0.5
#                                and regenerate docs/, then commit (and push if
#                                this machine can)
#     build/update.sh --publish  the last step alone - run by the build unit
#                                once the images and docs are written
#
# v3.0.5 is the WORKING TREE: its pins are blank, and it means whatever
# libraries and OnlyKey-Firmware are checked out on bench-worktree. This moves
# those checkouts to his master and rebuilds, so "v3.0.5" keeps meaning his
# current tree - and every image records the commits it was built from, so the
# page can say exactly which that was.
#
# The build runs the way run-matrix.sh runs it, for the same two reasons that
# script documents: a transient `systemd --user` unit so it survives the ssh
# session, and `sg docker -c` because the user manager lacks the docker group.
# If a build still dies when you log out, the user manager is not lingering:
# `sudo loginctl enable-linger bmatusiak`.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$HERE")"
LIB="$ROOT/libraries"
FW="$ROOT/OnlyKey-Firmware"
UNIT="okfw-matrix"
REMOTE="0c-coder"
BRANCH="bench-worktree"
INDEX="$HERE/developer_firmware/index.json"
VARIANTS="v3.0.5-classic-test v3.0.5-classic-prod v3.0.5-duo-test v3.0.5-duo-prod"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

say() { echo "update: $*"; }
die() { echo "update: $*" >&2; exit 1; }

# The commits every v3.0.5 image records, as "libraries firmware" - or nothing
# if they do not all agree, or any image is missing or predates the recording.
recorded() {
  python3 - "$INDEX" $VARIANTS <<'PY'
import json, sys
path, names = sys.argv[1], sys.argv[2:]
try:
    idx = json.load(open(path))
except (OSError, ValueError):
    sys.exit(0)
seen = {(idx.get(n, {}).get("libraries"), idx.get(n, {}).get("firmware")) for n in names}
if len(seen) == 1:
    lib, fw = seen.pop()
    if lib and fw:
        print(lib, fw)
PY
}

publish() {
  cd "$HERE"
  local want lib fw
  want="$(git -C "$LIB" rev-parse --short=7 HEAD) $(git -C "$FW" rev-parse --short=7 HEAD)"
  # Only a COMPLETE set is committed. A variant that failed to build would leave
  # the page describing a mixture of two trees under one name.
  if [ "$(recorded)" != "$want" ]; then
    die "the four v3.0.5 images do not all record libraries/firmware $want - not committing; see developer_firmware/failures.json and the sweep log"
  fi
  read -r lib fw <<<"$want"
  git add developer_firmware docs
  if git diff --cached --quiet; then
    say "nothing changed to commit"
  else
    git commit -q -m "v3.0.5 working tree: libraries@$lib, OnlyKey-Firmware@$fw"
    say "committed $(git rev-parse --short HEAD)"
  fi
  # Prompts disabled, so a missing credential fails at once instead of hanging.
  if GIT_TERMINAL_PROMPT=0 git push -q origin HEAD 2>/dev/null; then
    say "pushed - the page will follow"
  else
    say "NOT pushed: this machine has no credential for origin. Push it from somewhere that does."
  fi
}

if [ "${1:-}" = "--publish" ]; then
  publish
  exit 0
fi

# --- refuse to start into a mess --------------------------------------------

if systemctl --user is-active --quiet "$UNIT"; then
  die "$UNIT is already running - one build at a time"
fi
for repo in "$LIB" "$FW"; do
  [ -z "$(git -C "$repo" status --porcelain)" ] \
    || die "$repo has uncommitted changes; moving $BRANCH would discard them"
done
# The builds repo is EXPECTED to have regenerated outputs lying around - they
# are what this commits. Only its code has to be clean.
if [ -n "$(git -C "$HERE" status --porcelain -- build package.json ok-versions.json docs/index.html)" ]; then
  die "uncommitted builder code in $HERE; commit it first so the build it produces is reproducible"
fi

# --- his master ---------------------------------------------------------------

for pair in "libraries:$LIB" "OnlyKey-Firmware:$FW"; do
  name="${pair%%:*}"; repo="${pair#*:}"
  if ! git -C "$repo" remote get-url "$REMOTE" >/dev/null 2>&1; then
    git -C "$repo" remote add "$REMOTE" "https://github.com/0c-coder/$name"
    # The same guard the Windows checkouts carry: fetch from him, never push.
    git -C "$repo" remote set-url --push "$REMOTE" DISABLED-do-not-push-to-0c-coder
    say "added fetch-only remote $REMOTE to $name"
  fi
  git -C "$repo" fetch -q "$REMOTE" master
done

his="$(git -C "$LIB" rev-parse --short=7 "$REMOTE/master") $(git -C "$FW" rev-parse --short=7 "$REMOTE/master")"
say "his master:       libraries/firmware $his"
say "images built from: $(recorded || true)"
if [ "$(recorded)" = "$his" ]; then
  say "already current - nothing to build"
  exit 0
fi

for repo in "$LIB" "$FW"; do
  git -C "$repo" checkout -q "$BRANCH"
  git -C "$repo" reset -q --hard "$REMOTE/master"
done
say "moved $BRANCH to his master in both checkouts"

# --- build, docs, commit - in one unit ----------------------------------------

LOGDIR="$HERE/developer_firmware/logs"
LOG="$LOGDIR/update-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$HERE/work" "$LOGDIR"
systemctl --user reset-failed "$UNIT" 2>/dev/null || true
systemd-run --user \
  --unit="$UNIT" \
  --description="OnlyKey firmware update to his master" \
  --working-directory="$HERE" \
  /bin/bash -c "sg docker -c 'python3 build/build.py --only v3.0.5 --rebuild && python3 build/make-docs.py && bash build/update.sh --publish' > '$LOG' 2>&1"

sleep 2
say "building v3.0.5 from libraries/firmware $his as $UNIT"
say "  log:    tail -f $LOG"
say "  status: http://$(hostname -I | awk '{print $1}'):8090"
