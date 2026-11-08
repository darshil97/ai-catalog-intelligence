"""Starts the one EC2 worker instance.

This has no permission to create instances, so duplicates are impossible. boto3 loads only
when first needed, to keep the API's memory use down when no start is required.
"""

from typing import Any

from app.compute.base import RETRY, STARTING, UNAVAILABLE, register_worker_controller
from app.config import get_compute_config
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Boot in progress: wait for its heartbeat.
_BOOTING_STATES = {"pending", "running"}
# Gone for good, and a person has to act.
_LOST_STATES = {"shutting-down", "terminated"}


def _make_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ec2",
        region_name=get_compute_config()["region"],
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )


class Ec2WorkerController:
    """Starts the configured instance if it's stopped."""

    name = "ec2"

    def __init__(self) -> None:
        """Set up the controller. The AWS client is created on first use."""
        self._client: Any = None

    def ensure_running(self) -> str:
        """Check the instance's state, and start it if stopped.

        Returns:
            STARTING if booting or just started, RETRY if it should be tried again later, or
            UNAVAILABLE if it's gone or not configured.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        instance_id = get_compute_config()["instance_id"]
        if not instance_id:
            logger.error("Worker start impossible: reason=instance_id_not_configured")
            return UNAVAILABLE
        if self._client is None:
            self._client = _make_client()

        try:
            response = self._client.describe_instances(InstanceIds=[instance_id])
            reservations = response.get("Reservations") or [{}]
            instances = reservations[0].get("Instances") or [{}]
            state = instances[0].get("State", {}).get("Name", "unknown")
            logger.info("Worker instance checked: instance_id=%s state=%s", instance_id, state)

            if state in _BOOTING_STATES:
                return STARTING
            if state in _LOST_STATES:
                logger.error("Worker instance lost: instance_id=%s state=%s", instance_id, state)
                return UNAVAILABLE
            if state != "stopped":
                # Usually 'stopping': it can't start until it has fully stopped.
                return RETRY

            self._client.start_instances(InstanceIds=[instance_id])
            logger.info("Worker instance start requested: instance_id=%s", instance_id)
            return STARTING
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            if code == "InvalidInstanceID.NotFound":
                logger.error("Worker instance not found: instance_id=%s", instance_id)
                return UNAVAILABLE
            # Includes stopping races and insufficient capacity. Queued jobs stay safe.
            logger.warning("Worker start failed: instance_id=%s code=%s", instance_id, code)
            return RETRY
        except BotoCoreError as exc:
            logger.warning("Worker start failed: error=%s", type(exc).__name__)
            return RETRY


register_worker_controller(Ec2WorkerController())
