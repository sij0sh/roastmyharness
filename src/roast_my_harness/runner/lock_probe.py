"""Back-compat alias over host_lock."""
from roast_my_harness.host_lock import ExperimentLock, lock_is_free

__all__ = ["ExperimentLock", "lock_is_free"]
