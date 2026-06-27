"""Built-in fixtures for oxitest.

Importing this package registers all built-in fixture implementations via
`BuiltinFixture.__init_subclass__`. `oxitest/__init__.py` triggers this
import so registrations are in place before any test runs.

Public type aliases (all carry `_FixtureMarker` so users write them bare,
without wrapping in `Fixture[...]`):

    tmp: TempDir              # unique temp directory, deleted after test
    factory: TempDirFactory   # session-scoped factory for multiple temp dirs
    cap: StdCapture           # capture sys.stdout / sys.stderr
    cap: FdCapture            # capture at fd level (C extensions, subprocesses)
    patch: Patcher            # temporary attribute / env / chdir overrides
    log: LogCapture           # capture logging records; log.records, log.text
    warn: WarnCapture         # capture warnings.warn() calls; warn.list, warn.clear()
"""

from __future__ import annotations

from typing import Annotated

# ── TestContext registration ──────────────────────────────────────────────────
# _TestContextFixture registers TestContext with BuiltinFixture._registry so
# the resolver in FixtureSession._inject_builtin() handles it alongside all
# other built-ins — no special-case needed in _fixture_session.py.
from oxitest._bridge._builtin_context import _BuiltinContext, _TestContext
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._builtins._capture import (
    CaptureResult as CaptureResult,
    _FdCapture,
    _FdCaptureFixture as _FdCaptureFixture,
    _StdCapture,
    _StdCaptureFixture as _StdCaptureFixture,
)
from oxitest._bridge._builtins._logcapture import (
    LogBackend as LogBackend,
    StdlibLogBackend as StdlibLogBackend,
    _LogCapture,
    _LogCaptureFixture as _LogCaptureFixture,
)
from oxitest._bridge._builtins._patch import (
    _Patcher,
    _PatcherFixture as _PatcherFixture,
)
from oxitest._bridge._builtins._tempdir import (
    _TempDir,
    _TempDirFactory,
    _TempDirFactoryFixture as _TempDirFactoryFixture,
    _TempDirFixture as _TempDirFixture,
)
from oxitest._bridge._builtins._warncapture import (
    _WarnCapture,
    _WarnCaptureFixture as _WarnCaptureFixture,
)


class _TestContextFixture(BuiltinFixture, fixture_type=_TestContext):
    def create(self, ctx: _BuiltinContext) -> _TestContext:
        return _TestContext(ctx.meta, ctx.teardown_stack)


# ── Public type aliases ───────────────────────────────────────────────────────
# Each alias IS already Annotated[..., _FixtureMarker()], so users write the
# name bare in annotations — no `Fixture[TempDir]` wrapping needed.

TempDir = _TempDir  # @injectable
TempDirFactory = _TempDirFactory  # @injectable
StdCapture = _StdCapture  # @injectable
FdCapture = _FdCapture  # @injectable
Patcher = _Patcher  # @injectable
TestContext = _TestContext  # @injectable
LogCapture = _LogCapture  # @injectable
WarnCapture = _WarnCapture  # @injectable

__all__ = [
    "TempDir",
    "TempDirFactory",
    "StdCapture",
    "FdCapture",
    "Patcher",
    "TestContext",
    "LogCapture",
    "WarnCapture",
    "LogBackend",
    "StdlibLogBackend",
    "CaptureResult",
    "BuiltinFixture",
    "_BuiltinContext",
]
