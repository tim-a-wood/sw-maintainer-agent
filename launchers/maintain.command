#!/bin/sh
set -eu

if command -v maintain >/dev/null 2>&1; then
  if [ -t 1 ]; then
    printf '\033]0;Software Maintainer\007'
    clear
  fi
  exec maintain "$@"
fi

printf '%s\n' 'Maintain is not installed. Follow the setup steps in README.md.' >&2
exit 2
