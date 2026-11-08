#!/bin/bash
# Powers off EC2 when the worker can't. A crashed worker can't stop its own machine, and nothing
# else would stop the bill. This runs separately from the worker, on a timer.
set -u

STALE_AFTER_SECONDS=900
MAX_UPTIME_SECONDS=$((8 * 3600))
MAINTENANCE_MARKER=/run/catalog-maintenance
MAINTENANCE_MAX_SECONDS=3600
HEARTBEAT_FILE=/run/catalog-worker/heartbeat

now=$(date +%s)
uptime_seconds=$(cut -d. -f1 /proc/uptime)

age_of() {
    echo $((now - $(stat -c %Y "$1")))
}

power_off() {
    echo "Watchdog powering off: reason=$1"
    /usr/bin/systemctl poweroff
    exit 0
}

# Allows deliberate work over Systems Manager, but only for a limited time.
if [ -e "$MAINTENANCE_MARKER" ] && [ "$(age_of "$MAINTENANCE_MARKER")" -lt "$MAINTENANCE_MAX_SECONDS" ]; then
    echo "Watchdog skipped: reason=maintenance"
    exit 0
fi

# Catches anything that keeps the worker busy forever. A running job recovers after the restart.
if [ "$uptime_seconds" -ge "$MAX_UPTIME_SECONDS" ]; then
    power_off "max_uptime"
fi

# Gives a fresh boot time to start up.
if [ "$uptime_seconds" -lt "$STALE_AFTER_SECONDS" ]; then
    exit 0
fi

if [ ! -e "$HEARTBEAT_FILE" ]; then
    power_off "no_heartbeat"
fi
if [ "$(age_of "$HEARTBEAT_FILE")" -ge "$STALE_AFTER_SECONDS" ]; then
    power_off "stale_heartbeat"
fi
