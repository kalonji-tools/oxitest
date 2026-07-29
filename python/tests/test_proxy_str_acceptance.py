"""Acceptance: cached fixture values render their own value, not the wrapper.

Runs the ``proxy_str`` data-project as a subprocess and reads the CTRF report
rather than the terminal summary — the question is which tests passed, not how
a reporter phrased the count. The project's own assertions are the acceptance
criteria; they fail if ``FrozenProxy`` stops forwarding string conversion.
"""

from __future__ import annotations

import json
from pathlib import Path

from oxitest import TempDir, helpers

_PROJECT = Path(__file__).parent / "data" / "proxy_str"

#: Every test the data-project declares, by name.
_EXPECTED = frozenset(
    {
        "test_shared_fixture_renders_its_value",
        "test_module_fixture_renders_its_value",
        "test_module_fixture_honours_format_spec",
    }
)


def test_cached_fixture_values_render_their_value(tmp: TempDir) -> None:
    """Every string conversion in the data-project renders the wrapped value."""
    report = Path(tmp) / "report.json"

    out, err, rc = helpers.common.run_oxitest(
        _PROJECT,
        "--serial",
        "--json",
        str(report),
    )

    assert rc == 0, (
        f"acceptance run failed (rc={rc}) — a fixture value rendered as "
        f"FrozenProxy(...) instead of its own value\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert report.exists(), (
        f"--json should create the report; without it the outcomes below are "
        f"unverifiable\nstdout:\n{out}\nstderr:\n{err}"
    )
    # CTRF names each test by node id ("<path>::<func>"); the path is this
    # checkout's absolute path, so key on the function name alone.
    outcomes = {
        t["name"].rpartition("::")[2]: t["status"]
        for t in json.loads(report.read_text())["results"]["tests"]
    }
    assert set(outcomes) == _EXPECTED, (
        f"the data-project did not run as written — expected {sorted(_EXPECTED)}, "
        f"got {sorted(outcomes)}; a missing test means rc == 0 proves nothing"
        f"\nstdout:\n{out}"
    )
    assert all(status == "passed" for status in outcomes.values()), (
        f"expected every rendering test to pass, got {outcomes} — a failure here "
        f"is FrozenProxy reporting itself instead of the fixture value"
        f"\nstdout:\n{out}"
    )
