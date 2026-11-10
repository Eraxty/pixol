#!/usr/bin/env bash
# Start/stop/status for the pixol Slack bot.
#
#   ./run.sh start     start detached, log to pixol.log
#   ./run.sh stop      stop it
#   ./run.sh restart   stop then start
#   ./run.sh status    is it running?
#   ./run.sh log       tail the log
set -u
cd "$(dirname "$0")" || exit 1

PY=.venv/bin/python
LOG=pixol.log
PIDFILE=.pixol.pid

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-start}" in
  start)
    if running; then
      echo "already running (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    setsid "$PY" app.py >"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    sleep 4
    if running; then
      echo "started (pid $(cat "$PIDFILE")) — log: $LOG"
    else
      echo "failed to start; last lines of $LOG:"
      tail -20 "$LOG"
      exit 1
    fi
    ;;
  stop)
    if running; then
      kill "$(cat "$PIDFILE")" && echo "stopped"
    else
      pkill -f "$PY app.py" && echo "stopped (pkill)" || echo "not running"
    fi
    rm -f "$PIDFILE"
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start
    ;;
  status)
    if running; then
      echo "running (pid $(cat "$PIDFILE"))"
    else
      echo "not running"
    fi
    tail -5 "$LOG" 2>/dev/null
    ;;
  log)
    tail -f "$LOG"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|log}" >&2
    exit 2
    ;;
esac
