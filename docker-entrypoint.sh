#!/usr/bin/env bash
# Run DevUI and the showcase side by side, and die together.
#
# There is no supervisor here on purpose: if either process dies the container
# should be replaced rather than serve a half-working demo, so this exits as
# soon as one of them does and lets the platform restart it.
set -uo pipefail

term() {
  trap - TERM INT
  kill -TERM "${DEVUI_PID:-}" "${WEB_PID:-}" 2>/dev/null || true
  wait
  exit 0
}
trap term TERM INT

python -m chaos_to_symphony.devui_app &
DEVUI_PID=$!

python -m chaos_to_symphony.api &
WEB_PID=$!

echo "devui pid=$DEVUI_PID  showcase pid=$WEB_PID"

# Returns as soon as either exits.
wait -n
echo "a process exited; stopping the container so it gets replaced" >&2
kill -TERM "$DEVUI_PID" "$WEB_PID" 2>/dev/null || true
wait
exit 1
