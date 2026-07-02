from __future__ import annotations

__all__ = ["TempDir", "TempDirFactory", "_TempDirFactoryFixture", "_TempDirFixture"]

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_type import injectable
from oxitest._bridge.result import StatusKind

# Statuses where the test did NOT fail — TempDir can be cleaned up safely.
# Note: TIMEOUT and XPASSED are NOT included because they are failures
# (timeout = hard failure, xpassed = unexpected pass which may indicate a bug).
_PASS_STATUSES = frozenset(
    {
        StatusKind.PASSED,
        StatusKind.SKIPPED,
        StatusKind.WARNED,
        StatusKind.XFAILED,
    }
)


def _should_keep(mode: str, result_cell: list | None) -> bool:
    """Decide whether to preserve a TempDir based on mode and test outcome."""
    if mode == "always":
        return True
    # mode == "failed": preserve only on failure
    if result_cell is None or result_cell[0] is None:
        return False
    return result_cell[0].status not in _PASS_STATUSES


@injectable
@dataclass(frozen=True, slots=True)
class TempDir:
    """A temporary directory provided to a test.

    Created fresh for each test and deleted (with all contents) after the test
    completes. Use ``--keep-tmp`` to preserve the directory on failure for
    debugging (see :ref:`builtins-keep-tmp`).

    Use the `.path` attribute to get a `pathlib.Path`, or use the object
    directly anywhere a path is accepted (`/` operator, `os.fspath`, `str`).

    Example:
        ```python
        def test_writes_file(tmp: TempDir) -> None:
            output = tmp / "result.txt"
            output.write_text("hello")
            assert output.read_text() == "hello"
        ```
    """

    path: Path

    def __truediv__(self, other: str | Path) -> Path:
        return self.path / other

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)


@injectable
class TempDirFactory:
    """Session-scoped factory for creating multiple named temp directories.

    Injected as `factory: TempDirFactory`. Each call to `mktemp` returns a
    new `TempDir` with a unique name. All directories are deleted at the end
    of the test session.

    Example:
        ```python
        def test_two_dirs(factory: TempDirFactory) -> None:
            src = factory.mktemp("src")
            dst = factory.mktemp("dst")
            shutil.copy(src / "a.txt", dst / "a.txt")
        ```
    """

    def __init__(self) -> None:
        self._dirs: list[Path] = []

    def mktemp(self, label: str) -> TempDir:
        """Create a new temp directory and return it as a TempDir.

        Args:
            label: Short identifier embedded in the directory name for easier
                debugging. Does not need to be unique across calls.

        Returns:
            A new `TempDir` pointing at the created directory.
        """
        d = Path(tempfile.mkdtemp(prefix=f"{label}_"))
        self._dirs.append(d)
        return TempDir(d)

    def _cleanup(self) -> None:
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._dirs.clear()


class _TempDirFixture(BuiltinFixture, fixture_type=TempDir):
    def create(self, ctx: _BuiltinContext) -> TempDir:
        prefix = f"{ctx.fn_name}_" if ctx.fn_name else None
        d = Path(tempfile.mkdtemp(prefix=prefix))
        tmp = TempDir(d)

        if ctx.keep_tmp is None:
            ctx.teardown_stack.append(lambda: shutil.rmtree(d, ignore_errors=True))
        else:
            mode = ctx.keep_tmp
            cell = ctx.result_cell

            def _teardown() -> None:
                if _should_keep(mode, cell):
                    sys.stderr.write(f"KEPT {d} (--keep-tmp)\n")
                else:
                    shutil.rmtree(d, ignore_errors=True)

            ctx.teardown_stack.append(_teardown)
        return tmp


class _TempDirFactoryFixture(BuiltinFixture, fixture_type=TempDirFactory):
    scope = "session"

    def create(self, ctx: _BuiltinContext) -> TempDirFactory:
        factory = TempDirFactory()
        if ctx.keep_tmp is None:
            ctx.teardown_stack.append(factory._cleanup)
        else:
            # Session-scoped: no per-test result cell at teardown time.
            # Preserve all factory dirs when --keep-tmp is set (any mode).
            def _teardown() -> None:
                for d in factory._dirs:
                    sys.stderr.write(f"KEPT {d} (--keep-tmp)\n")

            ctx.teardown_stack.append(_teardown)
        return factory
