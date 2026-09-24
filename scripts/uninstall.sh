#!/bin/sh
# Legacy entry point — the implementation is ../uninstall.sh.
# `--force` / `--purge` mean `--yes` there.
exec bash "$(dirname "$0")/../uninstall.sh" "$@"
