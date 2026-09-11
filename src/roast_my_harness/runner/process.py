"""Back-compat alias over host_process."""
from roast_my_harness.host_process import VariantProcess, cancel_all, require_all_started

__all__ = ["VariantProcess", "cancel_all", "require_all_started"]
