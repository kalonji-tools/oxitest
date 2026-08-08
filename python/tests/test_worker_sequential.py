"""A Worker runs one Test Item at a time (#1967).

Three shipped things rest on this and nothing enforced it: ``Patcher``'s four
process-global surfaces, ``_cwd_guard``'s liveness predicate, and #1966's
reasoning about which teardown failures are recoverable. If intra-Worker
parallelism is ever added, all three become wrong silently — this test is what
makes that loud instead.

Grouping by pid is load-bearing: one pid is one Worker. Across the run as a
whole, tests genuinely do overlap, so an ungrouped assertion would be false by
construction under ``-n 2``.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "worker_sequential"


def _by_pid(events: tuple[str, ...]) -> dict[str, list[tuple[str, float, float]]]:
    """Group ``<pid> <ident> <start> <end> <name>`` records by pid."""
    grouped: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for line in events:
        pid, ident, start, end, _name = line.split()
        grouped[pid].append((ident, float(start), float(end)))
    return grouped


def test_one_worker_runs_one_test_item_at_a_time(tmp: TempDir) -> None:
    """Two assertions, because they fail differently.

    The ident assertion catches threads. The overlap assertion also catches an
    ``asyncio.gather`` — same thread, interleaved windows — which the ident
    check cannot see.
    """
    # Arrange / Act
    run = helpers.run_with_event_log(_PROJECT, tmp, "WS_LOG", "-n", "2")

    # Assert
    assert run.rc == 0, (
        f"the probe project must pass; rc={run.rc}\n{run.stdout}\n{run.stderr}"
    )
    assert len(run.events) == 6, (
        "every item must record, or the assertions below are about a subset "
        f"and pass vacuously; got {len(run.events)} records: {run.events}"
    )

    grouped = _by_pid(run.events)
    for pid, records in grouped.items():
        idents = {ident for ident, _start, _end in records}
        assert len(idents) == 1, (
            f"worker {pid} used {len(idents)} threads. Patcher's four surfaces "
            f"are process-global and safe only because one item runs at a "
            f"time; threads inside a worker break that silently: {records}"
        )

        windows = sorted((start, end) for _ident, start, end in records)
        for (_, earlier_end), (later_start, _) in pairwise(windows):
            assert earlier_end <= later_start, (
                f"worker {pid} overlapped two test windows. Same-thread "
                f"interleaving (an asyncio.gather, say) is invisible to the "
                f"thread-ident check above but breaks the same invariant: "
                f"{windows}"
            )
