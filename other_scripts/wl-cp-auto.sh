#!/usr/bin/env bash
# wl-cp-auto.sh — xremap handler for Ctrl+V.
#   1. Paste current clipboard file.
#   2. If a wl-cp-serial queue is active, signal the worker directly by PID.
# No dependency on wl-cp-serial being on PATH inside the service env.

set -u
FLAG="$HOME/.cache/wl-cp-serial-active"
LOG="$HOME/.cache/wl-cp-auto.log"

log() { printf '%s %s\n' "$(date +%FT%T)" "$*" >>"$LOG" 2>&1; }

log "called; flag=$( [[ -f $FLAG ]] && cat "$FLAG" 2>/dev/null || echo MISSING )"

# 1. Paste current clipboard contents.
if command -v ydotool >/dev/null 2>&1; then
    ydotool key 29:1 47:1 47:0 29:0 2>>"$LOG"; log "ydotool rc=$?"
elif command -v wtype >/dev/null 2>&1; then
    wtype -M ctrl -k v 2>>"$LOG"; log "wtype rc=$?"
else
    log "ERROR: neither ydotool nor wtype on PATH"
    exit 0
fi

# 2. Advance the queue after the paste has had time to read.
if [[ -f "$FLAG" ]]; then
    pid="$(cat "$FLAG" 2>/dev/null)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        (
            sleep 0.5
            kill -USR1 "$pid" 2>>"$LOG" && log "signaled $pid" \
                                         || log "kill USR1 $pid failed rc=$?"
        ) &
    else
        log "flag pid '$pid' not alive — stale?"
    fi
fi
