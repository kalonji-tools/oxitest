"""Exit 4 is decided by the class of the error, not by the transition (#2172).

``docs/user/reference/exit-codes.md`` defines exit 4 by the class of the error
and defines exit 3 as a test file that could not be imported, a declaration
inside one that was refused, or a strict violation. A startup failure is none of
those, so it must keep the class of its own error.
"""

import os
from pathlib import Path

from oxitest import TempDir
from oxitest._bridge._errors import InternalError, UsageError, is_usage_error
from tests import helpers

_PLUGIN_ENTRY = """\
from oxitest.plugin import Plugin


def oxitest_plugin(config=None):
    return Plugin()
"""

_CONTROL_TEST = """\
def test_ok():
    assert 1 == 1, "the control test pins that the scaffold itself runs"
"""


def _plugin_project(tmp: TempDir, namespace: str) -> Path:
    """Build a project whose plugin declares *namespace*. Return its root."""
    project = Path(str(tmp))
    (project / "my_plugin").mkdir()
    (project / "my_plugin" / "__init__.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text(_CONTROL_TEST, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["tests"]\n'
        'plugins = ["my_plugin"]\n'
        "\n"
        "[tool.oxitest.plugin_settings.my_plugin]\n"
        f'namespace = "{namespace}"\n',
        encoding="utf-8",
    )
    return project


def _run(project: Path) -> tuple[str, str, int]:
    """Run oxitest inside *project* with the plugin importable."""
    env = {**os.environ, "PYTHONPATH": str(project)}
    return helpers.run_oxitest(None, cwd=str(project), env=env)


def test_a_reserved_plugin_namespace_exits_usage_error(tmp: TempDir) -> None:
    """A plugin configuration error is an invalid request, which is exit 4."""
    # Arrange
    project = _plugin_project(tmp, "oxi")

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    assert rc == 4, (
        f"a plugin that claims the reserved namespace is an invalid request, and "
        f"exit-codes.md fixes exit 4 by the class of the error; exit 3 claims a "
        f"test file could not be imported, and this run imported none\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_a_valid_plugin_namespace_exits_success(tmp: TempDir) -> None:
    """CONTROL — the exit code must come from the refusal, not from the scaffold."""
    # Arrange
    project = _plugin_project(tmp, "mine")

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    assert rc == 0, (
        f"the same project without the reserved namespace must pass; if it does "
        f"not, the test above measures a broken scaffold and not the refusal\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_internal_error_is_not_a_usage_error() -> None:
    """A broken invariant is not an invalid request, so it must not vote exit 4."""
    # Arrange
    broken_invariant = InternalError("a broken invariant")

    # Act
    verdict = is_usage_error(broken_invariant)

    # Assert
    assert verdict is False, (
        "InternalError marks an oxitest bug, not a user's invalid request; if it "
        "voted exit 4 the user would be told to correct configuration that is "
        "already correct"
    )


def test_usage_error_votes_the_usage_exit_code() -> None:
    """The class named UsageError must vote the exit code named UsageError."""
    # Arrange
    invalid_request = UsageError("an invalid request")

    # Act
    verdict = is_usage_error(invalid_request)

    # Assert
    assert verdict is True, (
        "ADR-0014 fixes exit 4 by the class of the error; a class meaning 'a "
        "user-facing API is used incorrectly' that does not vote it leaves the "
        "two names disagreeing about one concept"
    )


_IDENTITY_MISUSE = """\
from oxitest import TestIdentity


def test_reads_its_own_identity(ident: TestIdentity) -> None:
    assert ident is not None, "the misuse is the subject of this test"
"""

_PADDING = "".join(
    f'def test_pad_{n}():\n    assert 1 == 1, "padding, to make the run parallel"\n'
    for n in range(6)
)


def test_the_exit_code_does_not_depend_on_the_execution_mode(tmp: TempDir) -> None:
    """A UsageError must not exit differently because a worker observed it.

    The plan's premise ledger named execution mode as the dimension none of its
    premises varied. ``_USAGE_ERROR_TYPES`` is read by the serial path and by
    ``worker.py`` alike, so a change to it reaches both. This pins that the two
    agree.

    Measured value today is 1, not 4: the vote is consulted at three sites in
    ``executor.py`` and ``_diagnostics.py``, and the path from a builtin
    fixture's refusal reaches none of them. This test deliberately asserts
    agreement and not a literal, because the literal is a separate defect and
    freezing it here would make this test refuse its own repair.
    """
    # Arrange
    project = Path(str(tmp))
    (project / "tests").mkdir()
    (project / "tests" / "test_identity.py").write_text(
        _IDENTITY_MISUSE, encoding="utf-8"
    )
    (project / "tests" / "test_pad.py").write_text(_PADDING, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["tests"]\nmin_parallel_tests = 1\n',
        encoding="utf-8",
    )

    # Act
    serial_out, serial_err, serial_rc = helpers.run_oxitest(
        None, "--serial", cwd=str(project)
    )
    par_out, par_err, par_rc = helpers.run_oxitest(None, "-n", "2", cwd=str(project))

    # Assert
    assert serial_rc == par_rc, (
        f"exit-codes.md fixes exit 4 by the class of the error and says nothing "
        f"about which process observed it; an exit code that depends on the "
        f"execution mode is the same defect this change removes, in a new place\n"
        f"serial={serial_rc} parallel={par_rc}\n"
        f"serial output:\n{serial_out}{serial_err}\n"
        f"parallel output:\n{par_out}{par_err}"
    )
    assert "TestIdentity is for a fixture" in (par_out + par_err), (
        f"the parallel arm must actually reach the refusal; without this the "
        f"comparison above passes on two runs that both did nothing\n"
        f"{par_out}{par_err}"
    )
