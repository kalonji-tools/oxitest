"""Shared test utilities for oxitest's own suite.

Plain functions, reached by ``from tests import helpers``. There is no helper
registry — #1700 retired the concept; mediation is justified by lifecycle,
never by visibility.

The submodules organise the code; this facade keeps call sites flat so a
reader writes ``helpers.run_oxitest(...)`` without tracking which file it
lives in.
"""

from __future__ import annotations

__all__ = [
    "EventLogRun",
    "RecordingDebugger",
    "assert_result",
    "exec_inline",
    "install_module",
    "make_exc",
    "make_fixture_def",
    "make_meta",
    "make_plugin_module",
    "make_registry",
    "make_session",
    "make_session_with",
    "read_event_log",
    "run_oxitest",
    "run_oxitest_subcmd",
    "run_test",
    "run_with_event_log",
    "write_test_file",
    "write_test_module",
]

from tests.helpers.event_logs import EventLogRun, read_event_log, run_with_event_log
from tests.helpers.factories import (
    make_exc,
    make_fixture_def,
    make_meta,
    make_registry,
    make_session,
    make_session_with,
)
from tests.helpers.modules import (
    install_module,
    make_plugin_module,
    write_test_file,
    write_test_module,
)
from tests.helpers.runners import (
    RecordingDebugger,
    assert_result,
    exec_inline,
    run_oxitest,
    run_oxitest_subcmd,
    run_test,
)
