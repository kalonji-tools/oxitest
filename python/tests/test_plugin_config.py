"""Tests for the typed plugin configuration system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import oxitest
from oxitest._bridge._plugin_config import (
    Both,
    Cli,
    Conf,
    IntrospectionError,
    introspect_config,
    merge_config,
)


def test_introspect_extracts_fields() -> None:
    """introspect_config extracts field names, sources, and order from a dataclass."""

    @dataclass(frozen=True)
    class Cfg:
        host: Annotated[str, Both(help="host")] = "local"
        debug: Annotated[bool, Cli(help="debug")] = False
        path: Annotated[str | None, Conf(help="path")] = None

    descs = introspect_config(Cfg)
    assert len(descs) == 3, f"expected 3 fields, got {len(descs)}"
    assert descs[0].name == "host", f"expected 'host', got '{descs[0].name}'"
    assert isinstance(descs[0].source, Both), (
        f"expected Both, got {type(descs[0].source)}"
    )
    assert descs[1].name == "debug", f"expected 'debug', got '{descs[1].name}'"
    assert isinstance(descs[1].source, Cli), (
        f"expected Cli, got {type(descs[1].source)}"
    )
    assert descs[2].name == "path", f"expected 'path', got '{descs[2].name}'"
    assert isinstance(descs[2].source, Conf), (
        f"expected Conf, got {type(descs[2].source)}"
    )


def test_introspect_rejects_unannotated_field() -> None:
    """introspect_config raises IntrospectionError for a field without annotation."""

    @dataclass(frozen=True)
    class Bad:
        name: str = "oops"

    with oxitest.raises(IntrospectionError, match="no source annotation"):
        introspect_config(Bad)


def test_introspect_rejects_non_dataclass() -> None:
    """introspect_config raises IntrospectionError when passed a plain class."""

    class NotDC:
        pass

    with oxitest.raises(IntrospectionError, match="not a dataclass"):
        introspect_config(NotDC)


def test_introspect_detects_optional() -> None:
    """introspect_config marks X | None fields as optional and others as required."""

    @dataclass(frozen=True)
    class Cfg:
        required: Annotated[str, Conf(help="required")]
        optional: Annotated[str | None, Conf(help="optional")] = None

    descs = introspect_config(Cfg)
    assert not descs[0].optional, "required field should not be optional"
    assert descs[1].optional, "str | None field should be optional"


def test_merge_precedence_cli_over_env_over_pyproject() -> None:
    """merge_config: CLI > env > pyproject precedence; command-line always wins."""

    @dataclass(frozen=True)
    class Cfg:
        host: Annotated[str, Both(help="host", env="TEST_HOST")] = "default"

    descs = introspect_config(Cfg)

    result = merge_config(
        Cfg, descs, pyproject_values={"host": "pyproject"}, cli_values={}, env_values={}
    )
    assert result.host == "pyproject", f"expected pyproject value, got {result.host}"

    result = merge_config(
        Cfg,
        descs,
        pyproject_values={"host": "pyproject"},
        cli_values={},
        env_values={"TEST_HOST": "env"},
    )
    assert result.host == "env", f"expected env override, got {result.host}"

    result = merge_config(
        Cfg,
        descs,
        pyproject_values={"host": "pyproject"},
        cli_values={"host": "cli"},
        env_values={"TEST_HOST": "env"},
    )
    assert result.host == "cli", f"expected CLI override, got {result.host}"


def test_merge_cli_only_ignores_pyproject() -> None:
    """A Cli-annotated field ignores pyproject values; only settable via the CLI."""

    @dataclass(frozen=True)
    class Cfg:
        verbose: Annotated[bool, Cli(help="verbose")] = False

    descs = introspect_config(Cfg)
    result = merge_config(
        Cfg, descs, pyproject_values={"verbose": True}, cli_values={}, env_values={}
    )
    assert result.verbose is False, "Cli-only field should ignore pyproject values"


def test_merge_conf_only_ignores_cli() -> None:
    """A Conf-annotated field ignores CLI values; only settable via pyproject.toml."""

    @dataclass(frozen=True)
    class Cfg:
        path: Annotated[str, Conf(help="path")] = "/default"

    descs = introspect_config(Cfg)
    result = merge_config(
        Cfg,
        descs,
        pyproject_values={"path": "/pyproject"},
        cli_values={"path": "/cli"},
        env_values={},
    )
    assert result.path == "/pyproject", "Conf-only field should ignore CLI values"


def test_merge_missing_required_field_raises() -> None:
    """merge_config raises ValueError when a required field has no value anywhere."""

    @dataclass(frozen=True)
    class Cfg:
        required: Annotated[str, Conf(help="required")]

    descs = introspect_config(Cfg)
    with oxitest.raises(ValueError, match="required"):
        merge_config(Cfg, descs, pyproject_values={}, cli_values={}, env_values={})
