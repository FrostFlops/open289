#!/bin/sh
# POSIX / Git Bash shim: ./open289.sh live -n 10
cd "$(dirname "$0")" && exec python -X utf8 -m open289 "$@"
