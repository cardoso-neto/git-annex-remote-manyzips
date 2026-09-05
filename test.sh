#!/bin/sh
set -eu

uv run --frozen coverage run -m unittest discover -s tests -v
uv run --frozen coverage report
