"""Exit 4 is decided by the class of the error, not by the transition (#2172).

``docs/user/reference/exit-codes.md`` defines exit 4 by the class of the error
and defines exit 3 as a test file that could not be imported, a declaration
inside one that was refused, or a strict violation. A startup failure is none of
those, so it must keep the class of its own error.
"""

import os
from pathlib import Path

from oxitest import TempDir
from oxitest._bridge._errors import (
    BackendNotFoundError,
    InternalError,
    PluginLoadError,
    PluginNotFoundError,
    UsageError,
    is_usage_error,
)
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


_BROKEN_DEP_PLUGIN = "import nonexistent_dependency_xyz\n"

_RAISING_PLUGIN = (
    'def oxitest_plugin(config=None):\n    raise RuntimeError("author bug")\n'
)


def _project_with_plugins(tmp: TempDir, plugins: str) -> Path:
    """Build a project whose pyproject names *plugins*. Return its root."""
    project = Path(str(tmp))
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text(_CONTROL_TEST, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        f'[tool.oxitest]\ntestpaths = ["tests"]\nplugins = [{plugins}]\n',
        encoding="utf-8",
    )
    return project


def test_an_absent_plugin_exits_usage_error(tmp: TempDir) -> None:
    """A plugin that is not installed exits ExitCode::UsageError."""
    # Arrange
    project = _project_with_plugins(tmp, '"absent_plugin_xyz_12345"')

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    assert rc == 4, (
        f"a plugins entry naming a module that is not installed is an invalid "
        f"request, which is ExitCode::UsageError; exit 3 claims a test file "
        f"could not be imported, and this run imported none\n{stdout}{stderr}"
    )


def test_an_absent_parent_package_exits_usage_error(tmp: TempDir) -> None:
    """A dotted plugin name whose parent package is absent.

    ``ImportError.name`` reports the first absent segment, so it holds
    ``absent_pkg_xyz`` and not ``absent_pkg_xyz.sub``. A predicate comparing the
    two for equality sends this down the defective-plugin arm and exits 3.
    """
    # Arrange
    project = _project_with_plugins(tmp, '"absent_pkg_xyz.sub"')

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    assert rc == 4, (
        f"the plugin is not installed, whatever segment of its dotted name is "
        f"the absent one; ExitCode::UsageError is decided by the plugin being "
        f"absent, not by the shape of its name\n{stdout}{stderr}"
    )


def test_a_defective_plugin_exits_collect_error(tmp: TempDir) -> None:
    """A plugin whose entry point raises keeps ExitCode::CollectError."""
    # Arrange
    project = _project_with_plugins(tmp, '"raising_plugin"')
    (project / "raising_plugin").mkdir()
    (project / "raising_plugin" / "__init__.py").write_text(
        _RAISING_PLUGIN, encoding="utf-8"
    )

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    assert rc == 3, (
        f"a plugin entry point that raises is the plugin author's bug; "
        f"ExitCode::UsageError would tell the user to correct a pyproject.toml "
        f"that is already correct\n{stdout}{stderr}"
    )


def test_an_installed_plugin_with_an_absent_dependency_names_it(tmp: TempDir) -> None:
    """The message names the dependency, not the plugin."""
    # Arrange
    project = _project_with_plugins(tmp, '"dep_plugin"')
    (project / "dep_plugin").mkdir()
    (project / "dep_plugin" / "__init__.py").write_text(
        _BROKEN_DEP_PLUGIN, encoding="utf-8"
    )

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    combined = stdout + stderr
    assert "Is it installed?" not in combined, (
        f"the plugin is installed; asking whether it is installed sends the "
        f"user to check the one thing that is already true\n{combined}"
    )
    assert "nonexistent_dependency_xyz" in combined, (
        f"the message must name the module that is absent, which is the "
        f"plugin's dependency and not the plugin\n{combined}"
    )
    assert rc == 3, (
        f"a plugin whose own import fails is defective, which is "
        f"ExitCode::CollectError\n{combined}"
    )


def test_a_plugin_raising_a_bare_import_error_is_defective(tmp: TempDir) -> None:
    """An ImportError naming no module takes the defective arm, not the absent one.

    ``ImportError.name`` is ``None`` when a plugin's own body raises
    ``ImportError`` directly. The plugin is installed, so claiming it is not
    would send the user to check the one thing that is already true.
    """
    # Arrange
    project = _project_with_plugins(tmp, '"bare_plugin"')
    (project / "bare_plugin").mkdir()
    (project / "bare_plugin" / "__init__.py").write_text(
        'raise ImportError("the plugin raises a bare ImportError")\n', encoding="utf-8"
    )

    # Act
    stdout, stderr, rc = _run(project)

    # Assert
    combined = stdout + stderr
    assert "Is it installed?" not in combined, (
        f"the plugin is installed and raised on import; ImportError.name being "
        f"None says nothing about whether the plugin is present\n{combined}"
    )
    assert rc == 3, (
        f"an ImportError that names no module cannot show the plugin is absent, "
        f"so the run keeps ExitCode::CollectError rather than claiming the "
        f"user's pyproject.toml is wrong\n{combined}"
    )


def test_an_absent_plugin_is_a_usage_error() -> None:
    """A plugin that is not installed is a value naming something absent."""
    # Arrange
    absent = PluginNotFoundError('plugin "x" not found. Is it installed?')

    # Act
    verdict = is_usage_error(absent)

    # Assert
    assert verdict is True, (
        "ADR-0008's amendment makes a [tool.oxitest] value that names something "
        "absent an invalid request, which exit-codes.md fixes at ExitCode 4"
    )


def test_a_defective_plugin_is_not_a_usage_error() -> None:
    """A plugin author's bug is not the user's invalid request."""
    # Arrange
    defective = PluginLoadError('plugin "x" oxitest_plugin() raised: boom')

    # Act
    verdict = is_usage_error(defective)

    # Assert
    assert verdict is False, (
        "exit 4 tells the user to correct their request; a plugin whose entry "
        "point raises is not something the user's pyproject.toml can correct"
    )


def test_an_absent_async_backend_is_a_usage_error() -> None:
    """async_backend names a backend that does not exist."""
    # Arrange
    absent = BackendNotFoundError("no_such_backend")

    # Act
    verdict = is_usage_error(absent)

    # Assert
    assert verdict is True, (
        "ADR-0014 makes a value naming something absent a usage error; a backend "
        "name in [tool.oxitest] is that shape one file away from a Target"
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
