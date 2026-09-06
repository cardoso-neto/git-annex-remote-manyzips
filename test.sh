#!/bin/sh
set -eu

for command in git git-annex; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required end-to-end test command not found: $command" >&2
        exit 1
    fi
done

uv run ruff format --check .
uv run ruff check .
uv run python -m unittest discover -s tests -v
