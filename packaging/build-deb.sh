#!/usr/bin/env bash
# Usage: packaging/build-deb.sh [output-dir]
# Needs uv, nfpm and the target system interpreter at /usr/bin/python3.
set -euo pipefail

cd "$(dirname "$0")/.."
output="${1:-dist}"
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' src/live_dictate/__init__.py)"

rm -rf build
mkdir -p "$output"
uv build --wheel --out-dir build/wheel
# Dependencies come from uv.lock with verified hashes, so every build bundles the same versions.
uv export --locked --no-dev --no-emit-project --format requirements.txt --output-file build/requirements.txt
uv pip install --python /usr/bin/python3 --target build/lib --compile-bytecode \
    --require-hashes --requirement build/requirements.txt
uv pip install --python /usr/bin/python3 --target build/lib --compile-bytecode --no-deps \
    build/wheel/live_dictate-"$version"-py3-none-any.whl
# Entry-point scripts point at the build interpreter path; /usr/bin/live-dictate replaces them.
rm -rf build/lib/bin build/lib/.lock

VERSION="$version" nfpm package \
    --config packaging/nfpm.yaml \
    --packager deb \
    --target "$output/live-dictate_${version}_amd64.deb"
