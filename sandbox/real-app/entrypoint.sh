#!/bin/sh
set -eu

Xvfb :99 -screen 0 800x600x24 -nolisten tcp -noreset >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!

attempt=0
while ! xdpyinfo -display :99 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 50 ]; then
    cat /tmp/xvfb.log >&2
    exit 1
  fi
  sleep 0.1
done

fluxbox -rc /usr/local/share/lotuspond-fluxbox-init >/tmp/fluxbox.log 2>&1 &
xterm -geometry 80x24+20+20 -title "Gym Anything Hello" \
  -e /usr/local/bin/capture-greeting.sh &

wait "$xvfb_pid"
