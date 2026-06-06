from __future__ import annotations

__all__ = [
    # Public API
    "Fixtures",
    "FixtureDef",
    "FixtureRegistry",
    "FixtureSession",
    "FixtureShadowWarning",
    "FixtureTeardownWarning",
    "UnannotatedFixtureParamError",
    # Internal bridge protocol (used by executor and loader)
    "_SessionProtocol",
    "_fixture_inner_type",
    "_fixture_ref_inner_type",
    "_Scope",
]


from oxitest._bridge._builtin_context import _TestContext as _TestContext
from oxitest._bridge._errors import (  # noqa: F401
    FixtureCycleError as FixtureCycleError,
    FixtureNotFoundError as FixtureNotFoundError,
    FixtureSetupError as FixtureSetupError,
    UnannotatedFixtureParamError as UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_registry import (
    FixtureDef as FixtureDef,
    FixtureRegistry as FixtureRegistry,
    FixtureShadowWarning as FixtureShadowWarning,
    _fixture_inner_type as _fixture_inner_type,
    _fixture_ref_inner_type as _fixture_ref_inner_type,
)
from oxitest._bridge._fixture_session import (
    BuiltinFixture as BuiltinFixture,
    FixtureAccessor as FixtureAccessor,
    Fixtures as Fixtures,
    FixtureSession as FixtureSession,
    FixtureTeardownWarning as FixtureTeardownWarning,
    _Scope as _Scope,
    _SessionProtocol as _SessionProtocol,
    _warn_teardown as _warn_teardown,
)
