"""Exit 4 is decided by the class of the error, not by the transition (#2172).

``docs/user/reference/exit-codes.md`` defines exit 4 by the class of the error
and defines exit 3 as a test file that could not be imported, a declaration
inside one that was refused, or a strict violation. A startup failure is none of
those, so it must keep the class of its own error.
"""

import os
from pathlib import Path

from oxitest import TempDir
from oxitest._bridge._errors import InternalError, is_usage_error
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
