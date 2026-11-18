#!/bin/sh
# IBKR Dashboard — scheduler watchdog sidecar.
#
# Recovers the exact failure seen 2026-08-15/17: the backend's APScheduler went
# silent for ~31 h while its HTTP server stayed up, so neither the Docker
# healthcheck (HTTP /health) nor `restart: unless-stopped` (fires only on
# process exit) noticed. This runs as a SEPARATE container/process, so an
# in-process wedge of the backend cannot wedge it too.
#
# Restart the backend ONLY for faults a restart can actually fix:
#   * scheduler.heartbeat_age_seconds > HEARTBEAT_STALE_SECONDS  → main loop wedged, or
#   * scheduler.last_pull_attempt_age_seconds > PULL_ATTEMPT_STALE_SECONDS while the
#     heartbeat is FRESH → the pull JOBS stopped being dispatched though the loop
#     still ticks. This is the 2026-08-22 recurrence: the pull jobs' max_instances
#     slot wedged and every fire was silently skipped, while the independent 5-min
#     heartbeat kept the heartbeat age fresh — invisible to the check above. The
#     attempt stamp is written at each pull job's START (before its network call),
#     so an UPSTREAM failure still refreshes it → this fires only on a genuine
#     dispatch wedge, never on a token/query error (no restart-loop). Relies on a
#     pull firing more often than the threshold (TC is hourly); if you make pulls
#     less frequent, raise PULL_ATTEMPT_STALE_SECONDS above the largest real gap.
#   * the status endpoint is unreachable UNREACHABLE_STRIKES times → hung / crashed.
# staleness.is_stale (last successful pull >30 h) is deliberately NOT a restart
# trigger: it means an UPSTREAM problem (expired Flex token, deactivated query,
# IBKR outage) that a restart cannot fix — restarting on it would bounce a
# healthy backend forever. The in-app staleness watchdog already alerts on it;
# here it is logged only.
# BEFORE restarting it captures a py-spy thread dump + faulthandler traceback of
# the backend into $DUMP_DIR (the freeze leaves no logs — this is the only way to
# root-cause it). After the restart it holds off RESTART_COOLDOWN_SECONDS. It
# does not pull itself: the backend's startup catch-up re-runs a missed daily
# slot, and the hourly TC job resumes on its cron.
#
# Deps: docker CLI + /var/run/docker.sock (restart/exec/dump), busybox wget (status).
set -u
trap 'exit 0' TERM INT   # exit promptly on `docker stop` (PID 1, no init)

BACKEND_URL="${BACKEND_URL:-http://backend:8000}"
TARGET="${TARGET_CONTAINER:-ibkr-backend}"
INTERVAL="${CHECK_INTERVAL:-300}"
HEARTBEAT_STALE="${HEARTBEAT_STALE_SECONDS:-1800}"
# Max age of the last pull-job DISPATCH before we treat it as a wedge (heartbeat
# fresh but pulls not firing). Default 3h = 3 missed hourly TC fires, well clear
# of a transient blip. Set 0 to disable this trigger.
PULL_ATTEMPT_STALE="${PULL_ATTEMPT_STALE_SECONDS:-10800}"
COOLDOWN="${RESTART_COOLDOWN_SECONDS:-5400}"
UNREACHABLE_STRIKES="${UNREACHABLE_STRIKES:-3}"
DRY_RUN="${DRY_RUN:-0}"
DUMP_DIR="${DUMP_DIR:-/dumps}"

log() { echo "[watchdog $(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }
# Tolerant of optional whitespace after the colon (compact today, but a future
# pretty-printed response must not silently fail-open to "never restart").
json_num()  { printf '%s' "$1" | grep -oE "\"$2\"[[:space:]]*:[[:space:]]*-?[0-9]+"        | head -1 | grep -oE -- '-?[0-9]+$'; }
json_bool() { printf '%s' "$1" | grep -oE "\"$2\"[[:space:]]*:[[:space:]]*(true|false)"    | head -1 | grep -oE '(true|false)$'; }

last_restart=0
unreachable=0

log "start: target=$TARGET url=$BACKEND_URL interval=${INTERVAL}s heartbeat_stale=${HEARTBEAT_STALE}s pull_attempt_stale=${PULL_ATTEMPT_STALE}s cooldown=${COOLDOWN}s dry_run=$DRY_RUN"
if docker inspect -f '{{.State.Status}}' "$TARGET" >/dev/null 2>&1; then
    log "docker socket OK; target status=$(docker inspect -f '{{.State.Status}}' "$TARGET" 2>/dev/null)"
else
    log "WARNING: cannot reach docker socket or target '$TARGET' — restarts will fail"
fi

# Capture a thread-stack dump of the (wedged) backend BEFORE restarting it — the
# freeze leaves no logs, so this is the only way to root-cause it. Non-destructive
# (py-spy reads memory read-only; SIGUSR1 just prints), so it also runs in DRY_RUN.
capture_dump() {
    reason="$1"
    if [ ! -d "$DUMP_DIR" ]; then
        log "  (no $DUMP_DIR mount — skipping diagnostic capture)"
        return 0
    fi
    ts=$(date -u '+%Y%m%dT%H%M%SZ')
    out="$DUMP_DIR/wedge-$ts"
    log "capturing diagnostics → $out.* ($reason)"
    if docker exec "$TARGET" py-spy dump --pid 1 > "$out.pyspy.txt" 2>&1; then
        log "  py-spy dump saved ($(wc -l < "$out.pyspy.txt" 2>/dev/null | tr -d ' ') lines)"
    else
        log "  py-spy dump FAILED (see $out.pyspy.txt)"
    fi
    if docker exec "$TARGET" python -c 'import os,signal; os.kill(1, signal.SIGUSR1)' 2>/dev/null; then
        log "  SIGUSR1 sent (faulthandler → backend stderr)"
    fi
    sleep 2
    docker logs "$TARGET" --tail 400 --timestamps > "$out.backendlog.txt" 2>&1 || true
    log "  backend log snapshot saved"
}

do_restart() {
    reason="$1"; now="$2"
    if [ "$last_restart" -ne 0 ]; then
        elapsed=$(( now - last_restart ))
        if [ "$elapsed" -lt "$COOLDOWN" ]; then
            log "SUPPRESSED ($reason): within cooldown (${elapsed}s < ${COOLDOWN}s), waiting for recovery"
            return 0
        fi
    fi
    last_restart="$now"   # stamp up front so even a failed restart honours the cooldown
    capture_dump "$reason"   # diagnose the wedge before we destroy the evidence with a restart
    if [ "$DRY_RUN" = "1" ]; then
        log "DRY-RUN would restart $TARGET ($reason) — diagnostics captured, not restarting"
        return 0
    fi
    log "RESTARTING $TARGET ($reason)"
    if docker restart "$TARGET" >/dev/null 2>&1; then
        # No recovery pull here: the backend's startup catch-up re-runs a missed
        # daily slot itself (+120 s, same job, digest included). A second pull
        # from here raced it on the same token and SQLite file (review of #17).
        log "restart issued; the backend catches up a missed daily slot on boot"
    else
        log "ERROR: docker restart $TARGET failed (will retry after cooldown)"
    fi
    return 0
}

while true; do
    now=$(date -u +%s)
    if body=$(wget -q -T 15 -O - "$BACKEND_URL/pipeline/status" 2>/dev/null); then
        unreachable=0
        is_stale=$(json_bool "$body" is_stale)
        data_is_stale=$(json_bool "$body" data_is_stale)   # datos congelados con pulls OK (2026-09-12): solo-log, lo avisa el backend
        hb_age=$(json_num "$body" heartbeat_age_seconds)
        pa_age=$(json_num "$body" last_pull_attempt_age_seconds)
        if [ -n "$hb_age" ] && [ "$hb_age" -gt "$HEARTBEAT_STALE" ]; then
            do_restart "scheduler wedged (heartbeat ${hb_age}s > ${HEARTBEAT_STALE}s)" "$now"
        elif [ "$PULL_ATTEMPT_STALE" -gt 0 ] && [ -n "$pa_age" ] && [ "$pa_age" -gt "$PULL_ATTEMPT_STALE" ]; then
            # Heartbeat fresh (loop alive) but no pull dispatched in too long =
            # pull-job dispatch wedge (2026-08-22). Restart fixes it; upstream
            # errors still stamp an attempt so they never land here (see header).
            do_restart "pull jobs wedged (no dispatch ${pa_age}s > ${PULL_ATTEMPT_STALE}s, heartbeat fresh ${hb_age:-none}s)" "$now"
        else
            # is_stale here (fresh heartbeat but old data) = upstream token/query/
            # IBKR problem — log only, never restart (see header).
            log "ok: is_stale=${is_stale:-?} data_is_stale=${data_is_stale:-?} heartbeat_age=${hb_age:-none}s pull_attempt_age=${pa_age:-none}s"
        fi
    else
        unreachable=$(( unreachable + 1 ))
        log "status unreachable (strike $unreachable/$UNREACHABLE_STRIKES)"
        if [ "$unreachable" -ge "$UNREACHABLE_STRIKES" ]; then
            do_restart "status endpoint unreachable x$unreachable" "$now"
            unreachable=0
        fi
    fi
    # `sleep` in the background + `wait`: a foreground sleep holds the TERM trap until it
    # ends, so `docker stop` waited 10 s and then SIGKILLed.
    sleep "$INTERVAL" &
    wait $!
done
