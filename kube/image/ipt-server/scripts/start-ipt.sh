#!/usr/bin/env sh
set -eu

python -m ipt_server.cli.ipdb prepare
exec python -m ipt_server.main
