"""Parametrize case resolution for oxitest test functions."""

from __future__ import annotations

__all__ = [
    "parametrize",
    "partial",
    "resolve_parametrize",
    "ParametrizeError",
    "_DictCases",
    "_DataclassCases",
    "_Partial",
    "_PartialCases",
]

import dataclasses
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, TypeVar, cast, get_args, get_origin, get_type_hints

from oxitest._bridge._errors import ParametrizeError
from oxitest._bridge._fn_metadata import get_metadata, get_or_create
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints
from oxitest._bridge.fixtures import _fixture_inner_type

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class _Partial:
    """A partial set of fields for a dataclass, used in parametrize composition."""

    target_type: type
    fields: dict[str, Any]
    provided_fields: frozenset[str]
    fixref_fields: list[str]


def partial(target_type: type, **fields: Any) -> _Partial:
    """Create a partial case value for parametrize composition.

    ``target_type`` must be a dataclass. Each keyword argument must name
    a valid field on the dataclass. At least one field is required.
    ``FixtureRef[T]`` fields must hold callables.

    Use with stacked ``@oxi.parametrize`` decorators::

        @oxi.parametrize(pg=oxi.partial(Case, db=pg_db))
        @oxi.parametrize(add=oxi.partial(Case, x=1, y=2))
        def test_math(db: Fixture[str], x: int, y: int) -> None: ...
    """
    from oxitest._bridge._fixture_type import FixtureRef, _FixtureRefMarker

    if not dataclasses.is_dataclass(target_type):
        raise TypeError(f"partial: {target_type!r} must be a dataclass")
    if not fields:
        raise TypeError("partial requires at least one field")

    valid_field_names = {f.name for f in dataclasses.fields(target_type)}
    unknown = set(fields.keys()) - valid_field_names
    if unknown:
        raise TypeError(
            f"partial({target_type.__name__}): unknown field(s)"
            f" {sorted(unknown)!r}\n"
            f"valid fields: {sorted(valid_field_names)!r}"
        )

    # Detect FixtureRef fields among provided fields
    mod = sys.modules.get(target_type.__module__)
    globalns = dict(vars(mod)) if mod else {}
    globalns.setdefault("FixtureRef", FixtureRef)
    field_hints = get_type_hints(target_type, globalns=globalns, include_extras=True)
    fixref_fields = [
        field_name
        for field_name in fields
        if field_name in field_hints
        and get_origin(field_hints[field_name]) is Annotated
        and any(
            isinstance(m, _FixtureRefMarker)
            for m in get_args(field_hints[field_name])[1:]
        )
    ]
    for field_name in fixref_fields:
        value = fields[field_name]
        if not callable(value):
            raise TypeError(
                f"partial({target_type.__name__}): field '{field_name}' is annotated"
                f" FixtureRef[...] but got {type(value)!r}"
                f" — pass a fixture function, e.g. {field_name}=my_fixture."
            )

    return _Partial(
        target_type=target_type,
        fields=dict(fields),
        provided_fields=frozenset(fields.keys()),
        fixref_fields=fixref_fields,
    )


@dataclass(frozen=True)
class _PartialCases:
    """Encapsulates partial-mode parametrize cases for one composition layer."""

    cases: dict[str, _Partial]
    param_type: type
    provided_fields: frozenset[str]
    fixref_fields: list[str]

    def items(self) -> Iterable[tuple[str, list[tuple[str, str]]]]:
        for case_id, p in self.cases.items():
            yield (
                case_id,
                [(k, repr(v)) for k, v in p.fields.items()],
            )


def _build_partial_cases(cases: dict[str, Any]) -> _PartialCases:
    """Validate and build a _PartialCases object from Partial values."""
    if not cases:
        raise TypeError("parametrize requires at least one case")
    first = next(iter(cases.values()))
    if not isinstance(first, _Partial):
        raise TypeError("internal error: expected _Partial instances")
    target_type = first.target_type
    for case_id, case in cases.items():
        if not isinstance(case, _Partial):
            raise TypeError(
                f"parametrize: all case values must be partial instances,"
                f" case '{case_id}' is {type(case)!r}."
                " Do not mix partial() with full dataclass instances."
            )
        if case.target_type is not target_type:
            raise TypeError(
                f"parametrize: all partial() calls must target the same dataclass type."
                f" Expected '{target_type.__name__}', case '{case_id}'"
                f" targets '{case.target_type.__name__}'."
            )
    provided = first.provided_fields
    fixref = first.fixref_fields
    return _PartialCases(
        cases=cases,
        param_type=target_type,
        provided_fields=provided,
        fixref_fields=fixref,
    )


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


@dataclass
class _DataclassCases:
    """Encapsulates dataclass-mode parametrize cases."""

    cases: dict[str, Any]
    param_type: type
    fixref_fields: list[str]  # precomputed at decoration time; invariant across cases

    @cached_property
    def fixref_names(self) -> frozenset[str]:
        return frozenset(self.fixref_fields)

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
        fixref_names = self.fixref_names
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

    Each keyword argument is a named test case. Case values must all be dicts,
    frozen dataclass instances, or ``partial()`` instances — mixing is not allowed.

    **Expanded mode** — use field-name parameters to receive individual values.
    Any parameter whose name matches a dataclass field (and is not annotated
    `Fixture[T]`) receives that field's value::

        @oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))
        def test_add(x: int, y: int, expected: int) -> None:
            assert x + y == expected

    **Compact mode** — annotate a single parameter with the dataclass type to
    receive the whole instance. oxitest detects compact mode when exactly one
    non-`Fixture[T]` parameter is annotated with the case type::

        @oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))
        def test_add(params: AddCase) -> None:
            assert params.x + params.y == params.expected

    **Composition mode** — use ``partial()`` with stacked decorators::

        @oxitest.parametrize(pg=oxi.partial(Case, db=pg_db))
        @oxitest.parametrize(add=oxi.partial(Case, x=1, y=2))
        def test_math(db: Fixture[str], x: int, y: int) -> None: ...

    The decorator itself is identical in both modes — the function signature
    expresses intent.
    """
    if not cases:
        raise TypeError("parametrize requires at least one case")

    first = next(iter(cases.values()))

    if isinstance(first, dict):

        def dict_decorator(fn: _F) -> _F:
            meta = get_or_create(fn)
            layer = _build_dict_cases(cases, fn)
            if meta.param_cases is not None:
                raise TypeError(
                    "parametrize: cannot mix dict-mode with stacked decorators."
                    " Use a single @parametrize call for dict mode."
                )
            meta.param_cases = (layer,)
            return fn

        return dict_decorator

    if isinstance(first, _Partial):
        new_layer = _build_partial_cases(cases)

        def partial_decorator(fn: _F) -> _F:
            meta = get_or_create(fn)
            existing = meta.param_cases
            if existing is None:
                meta.param_cases = (new_layer,)
            elif isinstance(existing, tuple):
                for layer in existing:
                    if not isinstance(layer, _PartialCases):
                        raise TypeError(
                            "parametrize: cannot mix partial() with"
                            " full dataclass or dict cases."
                            " All stacked @parametrize layers must use partial()."
                        )
                if existing[0].param_type is not new_layer.param_type:
                    raise TypeError(
                        "parametrize: all partial() calls must target the"
                        " same dataclass type."
                        f" Expected '{existing[0].param_type.__name__}',"
                        f" got '{new_layer.param_type.__name__}'."
                    )
                for layer in existing:
                    overlap = layer.provided_fields & new_layer.provided_fields
                    if overlap:
                        raise TypeError(
                            "parametrize: field overlap between layers:"
                            f" {sorted(overlap)!r}."
                            " Each layer must provide disjoint fields."
                        )
                meta.param_cases = (new_layer, *existing)
            return fn

        return partial_decorator

    if not dataclasses.is_dataclass(first):
        raise TypeError(
            "parametrize: case values must be dicts, frozen dataclass instances,"
            f" or partial() instances, got {type(first)!r}"
        )

    param_cases_layer = _build_dataclass_cases(cases)

    def dataclass_decorator(fn: _F) -> _F:
        meta = get_or_create(fn)
        if meta.param_cases is not None:
            raise TypeError(
                "parametrize: cannot mix full dataclass cases with stacked"
                " decorators. Use partial() for composition."
            )
        meta.param_cases = (param_cases_layer,)
        return fn

    return dataclass_decorator


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
    meta = get_metadata(fn_raw)
    layers = meta.param_cases if meta.param_cases is not None else _MISSING
    if layers is _MISSING:
        fn_name = getattr(fn_raw, "__name__", repr(fn_raw))
        raise ParametrizeError(
            f"resolve_parametrize: {fn_name!r} has no parametrize cases"
            f" but param_id={param_id!r} was requested."
            " Use @oxitest.parametrize to register cases."
        )
    layers = cast(tuple, layers)
    if len(layers) == 1 and not isinstance(layers[0], _PartialCases):
        return layers[0].resolve(fn, param_id)
    # Composition resolution — implemented in Task 4
    raise ParametrizeError("composition resolve not yet implemented")
