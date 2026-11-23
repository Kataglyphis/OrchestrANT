"""orchestrant.hub: the hub's modules, compiled into the wheel or read from a checkout (ANTfrastructure CON289)."""

from orchestrant import hub
from orchestrant.smoke import ort_profile


def test_a_checkout_reads_the_hub_file_itself() -> None:
    """A checkout never runs a stale copy from orchestrant/_hub."""
    module = hub.hub_module("verify_ort_profile")
    assert module.__file__ == str(hub.CHECKOUT_DIR / "verify_ort_profile.py")
    assert callable(module.operator_counts)


def test_the_bundle_counts_by_the_hubs_rule() -> None:
    """ort_profile's count is the hub's, memcpy left out."""
    events = [
        {
            "cat": "Node",
            "args": {"provider": "CPUExecutionProvider", "op_name": "Conv"},
        },
        {
            "cat": "Node",
            "args": {"provider": "CPUExecutionProvider", "op_name": "MemcpyToHost"},
        },
    ]
    assert dict(ort_profile.operator_counts(events)) == {"CPUExecutionProvider": 1}
