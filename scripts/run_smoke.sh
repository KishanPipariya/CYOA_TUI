#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/cyoa-tui-uv-cache}"

uv run pytest -q -m smoke
