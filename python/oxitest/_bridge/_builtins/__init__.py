"""Built-in fixtures for oxitest.

Importing this package registers all built-in fixture implementations via
`BuiltinFixture.__init_subclass__`. `oxitest/__init__.py` triggers this
import so registrations are in place before any test runs.

Public re-exports (all decorated with ``@injectable`` so users write them
bare, without wrapping in ``Fixture[...]``):

    tmp: TempDir              # unique temp directory, deleted after test
    factory: TempDirFactory   # session-scoped factory for multiple temp dirs
    cap: StdCapture           # capture sys.stdout / sys.stderr
    cap: FdCapture            # capture at fd level (C extensions, subprocesses)
    patch: Patcher            # temporary attribute / env / chdir overrides
    log: LogCapture           # capture logging records; log.records, log.text
    warn: WarnCapture         # capture warnings.warn() calls; warn.list, warn.clear()
"""

from __future__ import annotations

# ── TestContext registration ──────────────────────────────────────────────────
# _TestContextFixture registers TestContext with BuiltinFixture._registry so
# the resolver in FixtureSession._inject_builtin() handles it alongside all
# other built-ins — no special-case needed in _fixture_session.py.
from oxitest._bridge._builtin_context import (
    TestContext,
    _BuiltinContext,
)
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._builtins._capture import (
    CaptureResult as CaptureResult,
    FdCapture,
    StdCapture,
    _FdCaptureFixture as _FdCaptureFixture,
    _StdCaptureFixture as _StdCaptureFixture,
)
from oxitest._bridge._builtins._logcapture import (
    LogBackend as LogBackend,
    LogCapture,
    StdlibLogBackend as StdlibLogBackend,
    _LogCaptureFixture as _LogCaptureFixture,
)
from oxitest._bridge._builtins._patch import (
    Patcher,
    _PatcherFixture as _PatcherFixture,
)
from oxitest._bridge._builtins._tempdir import (
    TempDir,
    TempDirFactory,
    _TempDirFactoryFixture as _TempDirFactoryFixture,
    _TempDirFixture as _TempDirFixture,
)
from oxitest._bridge._builtins._warncapture import (
    WarnCapture,
    _WarnCaptureFixture as _WarnCaptureFixture,
)


class _TestContextFixture(BuiltinFixture, fixture_type=TestContext):
    def create(self, ctx: _BuiltinContext) -> TestContext:
        return TestContext(ctx.meta, ctx.teardown_stack)


__all__ = [
    "BuiltinFixture",
    "CaptureResult",
    "FdCapture",
    "LogBackend",
    "LogCapture",
    "Patcher",
    "StdCapture",
    "StdlibLogBackend",
    "TempDir",
    "TempDirFactory",
    "TestContext",
    "WarnCapture",
    "_BuiltinContext",
]
