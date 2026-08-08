"""Durations are measured with a high-resolution clock on every platform.

``time.monotonic()`` and ``time.perf_counter()` are the same syscall on Linux
and macOS, so the choice is invisible there. On Windows they are not:
``monotonic`` is ``GetTickCount64`` with ~15.6 ms granularity, while
``perf_counter`` is ``QueryPerformanceCounter`` with sub-microsecond
granularity.

Measuring durations with ``monotonic`` therefore reported **0.0 ms** on Windows
for anything faster than a scheduler tick — which is most fixtures and most
tests. That is not only a reporting defect: `worker.py` feeds the same number
into the timing cache the scheduler uses to balance workers, so the cost model
collapsed to all-zeros there (#1989).

This pins the decision rather than the observation. A test that measured a real
duration could not tell the two clocks apart on the platforms CI can assert on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest._bridge import _fixture_instantiator, worker


@dataclass(frozen=True)
class ClockCase:
    """One module that records durations, and a note on what it feeds."""

    module_name: str
    consumer: str


@oxi.parametrize(
    fixture_timing=ClockCase(
        module_name="_fixture_instantiator",
        consumer="fixture setup/teardown timings shown in reports",
    ),
    test_timing=ClockCase(
        module_name="worker",
        consumer="per-test durations, which feed the scheduler's timing cache",
    ),
)
def test_duration_sites_use_a_high_resolution_clock(
    *, module_name: str, consumer: str
) -> None:
    """Both duration recorders must reach for ``perf_counter``."""
    # Arrange
    module = {
        "_fixture_instantiator": _fixture_instantiator,
        "worker": worker,
    }[module_name]
    source = Path(module.__file__ or "")

    # Act
    text = source.read_text(encoding="utf-8")

    # Assert
    assert "time.monotonic()" not in text, (
        f"{module_name} records {consumer}; time.monotonic() has ~15.6 ms "
        "granularity on Windows, so every duration shorter than a scheduler "
        "tick is recorded as 0.0 there while looking correct on Linux"
    )
    assert "time.perf_counter()" in text, (
        f"{module_name} must measure {consumer} with the high-resolution clock; "
        "if the timing calls moved elsewhere, move this assertion with them "
        "rather than deleting it"
    )


def test_perf_counter_is_no_coarser_than_monotonic() -> None:
    """The swap can never be a downgrade on the platform running this."""
    # Act
    perf = time.get_clock_info("perf_counter").resolution
    mono = time.get_clock_info("monotonic").resolution

    # Assert
    assert perf <= mono, (
        "perf_counter is chosen for duration measurement precisely because it "
        f"is never coarser; here it reports {perf} against monotonic's {mono}, "
        "which would make the swap a regression on this platform"
    )
