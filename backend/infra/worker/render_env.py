"""Writes the worker's environment file on EC2, as root, before each worker start.

Secrets come from Parameter Store, and the file is readable only by root, because systemd
reads it and passes the values to the worker.
"""

import os
import subprocess
import urllib.request

import boto3

ENV_FILE = "/etc/catalog-worker/env"
METADATA = "http://169.254.169.254/latest"


def _instance_metadata(path: str) -> str:
    token_request = urllib.request.Request(  # noqa: S310
        f"{METADATA}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    with urllib.request.urlopen(token_request, timeout=5) as response:  # noqa: S310
        token = response.read().decode()
    request = urllib.request.Request(  # noqa: S310
        f"{METADATA}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token}
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return response.read().decode()


def main() -> None:
    """Write the environment file."""
    region = _instance_metadata("placement/region")
    ssm = boto3.client("ssm", region_name=region)
    database_url = ssm.get_parameter(Name="/catalog/prod/worker/database_url", WithDecryption=True)[
        "Parameter"
    ]["Value"]
    code_sha = subprocess.check_output(  # noqa: S603
        ["/usr/bin/git", "-C", "/opt/catalog/app", "rev-parse", "HEAD"], text=True
    ).strip()

    lines = {
        "APP_ENV": "production",
        "AWS_REGION": region,
        "DATABASE_URL": database_url.strip(),
        "WORKER_CODE_SHA": code_sha,
        "WORKER_INSTANCE_ID": _instance_metadata("instance-id"),
        "WORKER_POWER_CONTROLLER": "os_shutdown",
        "WORKER_HEARTBEAT_FILE": "/run/catalog-worker/heartbeat",
    }
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as env_file:
        env_file.writelines(f"{key}={value}\n" for key, value in lines.items())


if __name__ == "__main__":
    main()
