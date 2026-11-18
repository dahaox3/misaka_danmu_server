#!/usr/bin/env bash
# Run the packaged overrides on the matching Linux amd64 upstream runtime.
set -euo pipefail
archive=${1:?specify the built pakku release archive}
image=${2:?specify the matching upstream image}
here=$(cd "$(dirname "$0")" && pwd)
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "$archive" -C "$stage"
test -x "$stage/gateway/run-gateway.sh"
mounts=(-v "$here:/app/integrations/pakku:ro" -v "$stage/overrides/web-dist:/app/web/dist:ro")
while IFS= read -r -d '' path; do
    mounts+=(-v "$path:/app/${path#"$stage/overrides/"}:ro")
done < <(find "$stage/overrides/src" -type f -name '*.py' -print0)
for module in pakku_gateway pakku_entrypoint; do
    mounts+=(-v "$stage/gateway/$module.py:/app/$module.py:ro")
done
docker run --rm --network none --platform linux/amd64 "${mounts[@]}" \
    -e PYTHONPATH=/app:/app/integrations/pakku --entrypoint python "$image" -c \
    'import pakku_entrypoint, sys, unittest; suite = unittest.defaultTestLoader.discover("/app/integrations/pakku"); sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())'
