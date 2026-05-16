"""Parametrize case resolution for oxitest test functions."""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar, cast, get_args, get_origin, get_type_hints

from oxitest._bridge._errors import ParametrizeError
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge.fixtures import _fixture_inner_type

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class _DictCases:
    """Encapsulates dict-mode parametrize cases."""

    cases: dict[str, dict[str, Any]]

    def items(self) -> Iterable[tuple[str, list[tuple[str, str]]]]:
        for case_id, case in self.cases.items():
            yield case_id, [(str(k), repr(v)) for k, v in case.items()]

    def resolve(
        self, fn: Callable[..., Any], param_id: str
    ) -> tuple[dict[str, Any], frozenset[str]]:
        return dict(self.cases[param_id]), frozenset()


@dataclass(frozen=True)
class _DataclassCases:
    """Encapsulates dataclass-mode parametrize cases."""

    cases: dict[str, Any]
    param_type: type
    fixref_fields: list[str]  # precomputed at decoration time; invariant across cases

    def items(self) -> Iterable[tuple[str, list[tuple[str, str]]]]:
        for case_id, case in self.cases.items():
            yield (
                case_id,
                [
                    (f.name, repr(getattr(case, f.name)))
                    for f in dataclasses.fields(case)  # type: ignore[arg-type]
                ],
            )

    def resolve(
        self, fn: Callable[..., Any], param_id: str
    ) -> tuple[dict[str, Any], frozenset[str]]:
        case = self.cases[param_id]
        fixref_names = frozenset(self.fixref_fields)
        is_compact, compact_param = _detect_compact_mode(fn, case)
        if is_compact:
            if fixref_names:
                raise ParametrizeError(
                    "parametrize: compact mode is incompatible with FixtureRef fields"
                    f" ({', '.join(sorted(fixref_names))})."
                    " Use expanded mode — annotate individual fields in the test"
                    " function."
                )
            return {compact_param: case}, fixref_names
        param_kwargs = {
            f.name: getattr(case, f.name)
            for f in dataclasses.fields(case)  # type: ignore[arg-type]
        }
        return param_kwargs, fixref_names


def _detect_compact_mode(fn: Callable[..., Any], case: object) -> tuple[bool, str]:
    """Detect compact vs expanded parametrize mode from the function signature.

    Returns (is_compact, param_name). In expanded mode param_name is "".
    Compact mode: a non-Fixture[T] parameter is annotated with type(case).
    """
    case_type = type(case)
    hints = _get_hints(fn)
    matches: list[str] = [
        param_name
        for param_name, hint in hints.items()
        if param_name != "return"
        and not _fixture_inner_type(hint)[0]
        and hint is case_type
    ]
    if len(matches) > 1:
        raise ParametrizeError(
            "compact parametrize: multiple parameters annotated with"
            f" '{case_type.__name__}': {matches!r}. Use at most one."
        )
    if matches:
        return True, matches[0]
    return False, ""


def _build_dict_cases(cases: dict[str, Any], fn: Callable[..., Any]) -> _DictCases:
    """Validate and build a _DictCases object."""
    hints = _get_hints(fn)
    valid_keys = frozenset(
        name
        for name, hint in hints.items()
        if name != "return" and not _fixture_inner_type(hint)[0]
    )
    for case_id, case in cases.items():
        if not isinstance(case, dict):
            raise TypeError(
                f"parametrize dict mode: case '{case_id}' must be a dict,"
                f" got {type(case)!r}. All cases must be dicts or all must"
                " be dataclass instances — mixing is not allowed."
            )
        extra = set(case.keys()) - valid_keys
        if extra:
            raise TypeError(
                f"parametrize case '{case_id}': unexpected key(s)"
                f" {sorted(extra)!r}\n"
                f"valid keys: {sorted(valid_keys)!r}"
            )
        missing = valid_keys - set(case.keys())
        if missing:
            raise TypeError(
                f"parametrize case '{case_id}': missing key(s)"
                f" {sorted(missing)!r}\n"
                f"provided: {sorted(case.keys())!r}"
            )
    return _DictCases(cases=cases)


def _build_dataclass_cases(cases: dict[str, Any]) -> _DataclassCases:
    """Validate and build a _DataclassCases object."""
    from oxitest._bridge._fixture_type import FixtureRef, _FixtureRefMarker

    if not cases:
        raise TypeError("parametrize requires at least one case")
    first = next(iter(cases.values()))
    values_type = type(first)
    dc_params = getattr(values_type, "__dataclass_params__", None)
    if not getattr(dc_params, "frozen", False):
        raise TypeError(
            f"parametrize case type '{values_type.__name__}'"
            " must be a frozen dataclass.\n"
            "Hint: use @dataclass(frozen=True)"
        )
    for case_id, case in cases.items():
        if not isinstance(case, values_type):
            raise TypeError(
                f"parametrize case '{case_id}' must be an instance"
                f" of '{values_type.__name__}'"
            )
    mod = sys.modules.get(values_type.__module__)
    globalns = dict(vars(mod)) if mod else {}
    globalns.setdefault("FixtureRef", FixtureRef)
    field_hints = get_type_hints(values_type, globalns=globalns, include_extras=True)
    fixref_fields = [
        field_name
        for field_name, hint in field_hints.items()
        if get_origin(hint) is Annotated
        and any(isinstance(m, _FixtureRefMarker) for m in get_args(hint)[1:])
    ]
    for case_id, case in cases.items():
        for field_name in fixref_fields:
            value = getattr(case, field_name)
            if not callable(value):
                raise TypeError(
                    f"parametrize case '{case_id}': field '{field_name}' is annotated"
                    f" FixtureRef[...] but got {type(value)!r}"
                    f" — pass a fixture function, e.g. {field_name}=my_fixture."
                )
    return _DataclassCases(
        cases=cases, param_type=values_type, fixref_fields=fixref_fields
    )


def parametrize(**cases: Any) -> Callable[[_F], _F]:
    """Register named test cases on a test function.

    Each keyword argument is a named test case. Case values must all be dicts
    or all be frozen dataclass instances — mixing is not allowed.

    **Expanded mode** — use field-name parameters to receive individual values.
    Any parameter whose name matches a dataclass field (and is not annotated
    ``Fixture[T]``) receives that field's value::

        @oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))
        def test_add(x: int, y: int, expected: int) -> None:
            assert x + y == expected

    **Compact mode** — annotate a single parameter with the dataclass type to
    receive the whole instance. oxitest detects compact mode when exactly one
    non-``Fixture[T]`` parameter is annotated with the case type::

        @oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))
        def test_add(params: AddCase) -> None:
            assert params.x + params.y == params.expected

    The decorator itself is identical in both modes — the function signature
    expresses intent.
    """
    if not cases:
        raise TypeError("parametrize requires at least one case")

    first = next(iter(cases.values()))

    if isinstance(first, dict):

        def decorator(fn: _F) -> _F:
            setattr(fn, "_oxitest_param_cases", _build_dict_cases(cases, fn))
            return fn

        return decorator

    if not dataclasses.is_dataclass(first):
        raise TypeError(
            "parametrize: case values must be dicts or frozen dataclass instances,"
            f" got {type(first)!r}"
        )

    param_cases = _build_dataclass_cases(cases)

    def decorator(fn: _F) -> _F:
        setattr(fn, "_oxitest_param_cases", param_cases)
        return fn

    return decorator


_MISSING = object()


def resolve_parametrize(
    fn_raw: object,
    fn: Callable[..., Any],
    param_id: str | None,
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve a parametrize case into (param_kwargs, fixref_names).

    Returns ({}, frozenset()) for non-parametrized tests (param_id is None).
    Raises ParametrizeError on misconfiguration (e.g., compact + FixtureRef).
    """
    if param_id is None:
        return {}, frozenset()
    param_cases = getattr(fn_raw, "_oxitest_param_cases", _MISSING)
    if param_cases is _MISSING:
        fn_name = getattr(fn_raw, "__name__", repr(fn_raw))
        raise ParametrizeError(
            f"resolve_parametrize: {fn_name!r} has no '_oxitest_param_cases' attribute"
            f" but param_id={param_id!r} was requested."
            " Use @oxitest.parametrize to register cases."
        )
    return cast(_DictCases | _DataclassCases, param_cases).resolve(fn, param_id)
