#!/bin/bash
# Sets up the worker on EC2. First boot runs this as root. Safe to run again.
set -euo pipefail

BACKEND=/opt/catalog/app/backend
UNITS="$BACKEND/infra/worker/systemd"

id catalog >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin catalog

# The application stays owned by root, so the worker can't modify its own code.
export HOME=/root UV_PYTHON_PREFERENCE=only-system
cd "$BACKEND"
/opt/catalog/tools/bin/uv sync --frozen --no-dev --extra worker

install -d -m 755 /etc/catalog-worker
install -m 644 "$UNITS"/* /etc/systemd/system/
install -m 755 "$BACKEND/infra/worker/watchdog.sh" /opt/catalog/watchdog.sh
install -m 755 "$BACKEND/infra/worker/render_env.py" /opt/catalog/render_env.py

systemctl daemon-reload
systemctl enable catalog-poweroff.path catalog-watchdog.timer catalog-worker.service
systemctl start catalog-poweroff.path catalog-watchdog.timer
systemctl restart catalog-worker.service
