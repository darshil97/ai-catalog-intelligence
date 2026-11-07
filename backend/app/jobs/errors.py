"""Exceptions handlers and the queue use to say what happened to a job."""


class TransientJobError(Exception):
    """A temporary failure, such as throttling or a timeout. The job is retried."""


class PermanentJobError(Exception):
    """A failure retrying can't fix, such as a corrupt file. The job fails."""


class LeaseLostError(Exception):
    """This worker no longer owns the job, so it must not save anything."""


class JobInterruptedError(Exception):
    """The job must stop now, because the worker is stopping or lost its lease."""
