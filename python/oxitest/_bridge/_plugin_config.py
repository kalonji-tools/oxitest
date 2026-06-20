"""Typed plugin configuration: source markers, introspection, and config merging."""

from __future__ import annotations

import dataclasses
import os
import types
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class Cli:
    """Field is CLI-only. Not read from pyproject.toml."""

    help: str = ""
    short: str | None = None


@dataclass(frozen=True)
class Conf:
    """Field is pyproject.toml-only. No CLI flag generated."""

    help: str = ""


@dataclass(frozen=True)
class Both:
    """Field is both CLI and pyproject.toml. CLI overrides config."""

    help: str = ""
    short: str | None = None
    env: str | None = None


SourceMarker = Cli | Conf | Both


@dataclass(frozen=True)
class CliExtension:
    """Declares a plugin's CLI prefix and config type."""

    prefix: str
    config_type: type


@dataclass(frozen=True)
class FieldDescriptor:
    """Introspected field metadata from a config dataclass."""

    name: str
    python_type: type
    source: SourceMarker
    default: Any  # dataclasses.MISSING if required
    optional: bool


class IntrospectionError(Exception):
    """Raised when a plugin config dataclass is malformed."""


def introspect_config(config_type: type) -> list[FieldDescriptor]:
    """Extract field descriptors from a frozen config dataclass."""
    if not dataclasses.is_dataclass(config_type):
        raise IntrospectionError(f"{config_type.__name__} is not a dataclass")

    hints = get_type_hints(config_type, include_extras=True)
    descriptors: list[FieldDescriptor] = []

    for f in dataclasses.fields(config_type):
        hint = hints[f.name]
        source = _extract_source(f.name, hint)
        python_type, optional = _unwrap_type(hint)

        default = dataclasses.MISSING
        if f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()

        descriptors.append(
            FieldDescriptor(
                name=f.name,
                python_type=python_type,
                source=source,
                default=default,
                optional=optional,
            )
        )

    return descriptors


def merge_config(
    config_type: type,
    descriptors: list[FieldDescriptor],
    pyproject_values: dict[str, Any],
    cli_values: dict[str, Any],
    env_values: dict[str, str] | None = None,
) -> Any:
    """Merge pyproject + CLI + env into a validated config instance.

    Precedence: CLI > env > pyproject > default.
    """
    env_values = env_values if env_values is not None else dict(os.environ)
    merged: dict[str, Any] = {}

    for desc in descriptors:
        value = dataclasses.MISSING

        # Layer 1: default
        if desc.default is not dataclasses.MISSING:
            value = desc.default

        # Layer 2: pyproject (only for Conf and Both)
        if isinstance(desc.source, (Conf, Both)) and desc.name in pyproject_values:
            value = pyproject_values[desc.name]

        # Layer 3: env var (only for Both with env)
        if isinstance(desc.source, Both) and desc.source.env:
            env_val = env_values.get(desc.source.env)
            if env_val is not None:
                value = _coerce(desc, env_val)

        # Layer 4: CLI (only for Cli and Both)
        if isinstance(desc.source, (Cli, Both)):
            cli_val = cli_values.get(desc.name)
            if cli_val is not None:
                value = cli_val

        if value is dataclasses.MISSING:
            if desc.optional:
                value = None
            else:
                raise ValueError(
                    f"config field '{desc.name}' is required but no value provided "
                    f"(no default, not in pyproject, not on CLI)"
                )

        merged[desc.name] = value

    return config_type(**merged)


def _extract_source(field_name: str, hint: Any) -> SourceMarker:
    """Pull the Cli/Conf/Both annotation from an Annotated type."""
    if get_origin(hint) is not Annotated:
        raise IntrospectionError(
            f"field '{field_name}' has no source annotation. "
            f"Every field must be annotated with Cli, Conf, or Both."
        )

    for arg in get_args(hint):
        if isinstance(arg, (Cli, Conf, Both)):
            return arg

    raise IntrospectionError(
        f"field '{field_name}' is Annotated but has no Cli/Conf/Both marker."
    )


def _unwrap_type(hint: Any) -> tuple[type, bool]:
    """Unwrap Annotated[X | None, ...] -> (X, True) or Annotated[X, ...] -> (X, False).

    Returns (inner_type, is_optional).
    """
    inner = get_args(hint)[0] if get_origin(hint) is Annotated else hint

    origin = get_origin(inner)
    if origin is types.UnionType:
        args = [a for a in get_args(inner) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return inner, False

    return inner, False


def _coerce(desc: FieldDescriptor, raw: str) -> Any:
    """Coerce a string (from env) to the field's type."""
    if desc.python_type is bool:
        return raw.lower() in ("1", "true", "yes")
    if desc.python_type is int:
        return int(raw)
    if desc.python_type is float:
        return float(raw)
    return raw
