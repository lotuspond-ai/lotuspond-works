#!/bin/sh
set -eu

printf 'Type a greeting, then press Enter:\n'
IFS= read -r greeting
printf '%s\n' "$greeting" > /tmp/app-transcript.txt
printf 'Captured: %s\n' "$greeting"
while :; do sleep 3600; done
