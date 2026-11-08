#!/bin/bash
# Deploys one commit to the worker, and rolls back if it doesn't come up healthy.
#
# Run as root by the catalog-worker-deploy document, which has already confirmed the commit is
# on main and runs this file as it exists in that commit. Usage: deploy.sh <40-character SHA>
set -euo pipefail

SHA="$1"
APP=/opt/catalog/app
BACKEND="$APP/backend"
ENV_FILE=/etc/catalog-worker/env
export HOME=/root UV_PYTHON_PREFERENCE=only-system

# Stops the watchdog powering off mid-deploy. It expires on its own after an hour.
touch /run/catalog-maintenance
trap 'rm -f /run/catalog-maintenance' EXIT

PREVIOUS=$(git -C "$APP" rev-parse HEAD)

switch_to() {
    git -C "$APP" checkout -q --detach "$1"
    (cd "$BACKEND" && /opt/catalog/tools/bin/uv sync -q --frozen --no-dev --extra worker)
}

check() {
    "$BACKEND/.venv/bin/python" "$BACKEND/worker.py" --env-file "$ENV_FILE" "$@"
}

echo "Deploying $SHA (previous $PREVIOUS)"
switch_to "$SHA"
if check --self-check && bash "$BACKEND/infra/worker/install.sh" && check --wait-for-version "$SHA"; then
    echo "Deployed $SHA"
    exit 0
fi

echo "Deploy failed, rolling back to $PREVIOUS"
switch_to "$PREVIOUS"
bash "$BACKEND/infra/worker/install.sh"
exit 1
