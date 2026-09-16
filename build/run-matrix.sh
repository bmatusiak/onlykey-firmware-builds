#!/usr/bin/env bash
#
# Launch a matrix sweep that survives you logging out.
#
#     ./build/run-matrix.sh                 the whole matrix, resuming
#     ./build/run-matrix.sh --only v3.0.2   one release
#     ./build/run-matrix.sh --rebuild       redo variants already recorded
#
# WHY THIS EXISTS AND IS NOT JUST `python3 build/build.py &`
#
# Two things kill a long sweep on this machine, and both are invisible until
# hours have been wasted.
#
# 1. logind reaps session processes. A build started over ssh and backgrounded
#    dies the moment that ssh connection ends - nohup and setsid included. The
#    status server was killed this way twice before it was moved to a unit. So
#    the sweep runs as a transient systemd --user unit, which outlives the
#    session that started it.
#
# 2. The user manager does not have the docker group. systemd --user was
#    started at login, and `bmatusiak` was added to `docker` AFTER that, so the
#    manager and everything it spawns still run without it. Interactive ssh
#    sessions each get a fresh login and DO have it, which is exactly why this
#    worked by hand and failed as a unit. MEASURED:
#
#        groups seen by the unit: bmatusiak adm dialout ... (no docker)
#        plain docker ps:  DENIED
#        sg docker -c ...: OK
#
#    It presents as `docker run` exiting 126 with the real message - permission
#    denied on /var/run/docker.sock - buried in work/current.log rather than in
#    the sweep's own log. Every variant fails in under a second, so a sweep
#    "finishes" in twenty seconds with everything red.
#
#    `sg docker -c` re-evaluates group membership from /etc/group, so the build
#    gets the group without anyone logging out or rebooting. The alternative is
#    `loginctl terminate-user`, which would kill your editor session, or a
#    reboot - both worse than one wrapper.
#
# The sweep is resumable: variants already in out/index.json are skipped, so
# restarting after an interruption costs only what is missing.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="okfw-matrix"

# The sweep's own log goes in the repo, beside the per-build logs, and every
# sweep gets its own file rather than overwriting the last.
#
# The per-build logs say why one variant failed to compile. THIS one says what
# the sweep decided: which variants were skipped as already built, which were
# refused at their pin and for what reason, the order, the timings, and the
# final count. That is the record of a run, and it is the thing you want months
# later when asking why a particular image is or is not in the directory.
#
# Timestamped because sweeps are re-run - after a fix, after adding a release,
# after changing the axes - and comparing two runs is exactly when this is
# useful.
LOGDIR="$HERE/developer_firmware/logs"
LOG="$LOGDIR/sweep-$(date +%Y%m%d-%H%M%S).log"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if systemctl --user is-active --quiet "$UNIT"; then
  echo "!! $UNIT is already running. Watch its log with:"
  echo "     tail -f $(ls -t $LOGDIR/sweep-*.log | head -1)"
  echo "   or stop it with:"
  echo "     systemctl --user stop $UNIT"
  exit 1
fi
systemctl --user reset-failed "$UNIT" 2>/dev/null || true

mkdir -p "$HERE/work" "$LOGDIR"

# The arguments are passed through to build.py. Quoted with printf %q so a
# value containing a space survives the trip through sg's single -c string.
ARGS=""
for a in "$@"; do ARGS="$ARGS $(printf '%q' "$a")"; done

systemd-run --user \
  --unit="$UNIT" \
  --description="OnlyKey firmware matrix sweep" \
  --working-directory="$HERE" \
  /bin/bash -c "sg docker -c 'exec python3 build/build.py$ARGS' > '$LOG' 2>&1"

sleep 2
echo
echo "started as $UNIT"
echo "  log:     tail -f $LOG"
echo "  page:    http://$(hostname -I | awk '{print $1}'):8090"
echo "  stop:    systemctl --user stop $UNIT"
echo
echo "NOTE: systemd --user units stop when your last session on this machine"
echo "ends, unless lingering is on. To make it truly independent:"
echo "  sudo loginctl enable-linger $USER"
