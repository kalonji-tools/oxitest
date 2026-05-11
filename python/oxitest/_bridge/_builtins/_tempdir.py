from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from oxitest._bridge._builtins._base import BuiltinFixture, _BuiltinContext


@dataclass
class _TempDir:
    """A temporary directory provided to a test.

    Created fresh for each test and deleted (with all contents) after the test
    completes, regardless of pass or fail.

    Use the ``.path`` attribute to get a ``pathlib.Path``, or use the object
    directly anywhere a path is accepted (``/`` operator, ``os.fspath``, ``str``).

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


class _TempDirFactory:
    """Session-scoped factory for creating multiple named temp directories.

    Injected as ``factory: TempDirFactory``. Each call to ``mktemp`` returns a
    new ``TempDir`` with a unique name. All directories are deleted at the end
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

    def mktemp(self, label: str) -> _TempDir:
        """Create a new temp directory and return it as a TempDir.

        Args:
            label: Short identifier embedded in the directory name for easier
                debugging. Does not need to be unique across calls.

        Returns:
            A new ``TempDir`` pointing at the created directory.
        """
        d = Path(tempfile.mkdtemp(prefix=f"{label}_"))
        self._dirs.append(d)
        return _TempDir(d)

    def _cleanup(self) -> None:
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._dirs.clear()


class _TempDirFixture(BuiltinFixture, fixture_type=_TempDir):
    def create(self, ctx: _BuiltinContext) -> _TempDir:
        prefix = f"{ctx.fn_name}_" if ctx.fn_name else None
        d = Path(tempfile.mkdtemp(prefix=prefix))
        tmp = _TempDir(d)
        ctx.teardown_stack.append(lambda: shutil.rmtree(d, ignore_errors=True))
        return tmp


class _TempDirFactoryFixture(BuiltinFixture, fixture_type=_TempDirFactory):
    scope = "session"

    def create(self, ctx: _BuiltinContext) -> _TempDirFactory:
        factory = _TempDirFactory()
        ctx.teardown_stack.append(factory._cleanup)
        return factory
