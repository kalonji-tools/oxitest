"""Protocol implementations for the sample infra plugin."""

from __future__ import annotations

import logging
from typing import Any

from ._config import InfraConfig


class InfraHost:
    """Fixture value provided by InfraHostProvider."""

    def __init__(self, host: str, timeout: int) -> None:
        self.host = host
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"InfraHost({self.host!r}, timeout={self.timeout})"


class InfraHostProvider:
    """FixtureProvider: injects an InfraHost into tests."""

    def __init__(self, config: InfraConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "infra_host"

    @property
    def fixture_type(self) -> type:
        return InfraHost

    def create(self, _: Any) -> InfraHost:
        return InfraHost(self._config.host, self._config.timeout)

    def teardown(self, value: object) -> None:
        pass

    @property
    def scope(self) -> str:
        return "each"

    @property
    def autouse(self) -> bool:
        return False


class _RecordCollector(logging.Handler):
    """Internal handler that appends records to a shared list."""

    def __init__(self, records: list[Any]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


class InfraLogBackend:
    """LogBackend: captures infra-scoped log records."""

    def __init__(self) -> None:
        self._handler: _RecordCollector | None = None
        self._records: list[Any] = []

    def install(self) -> None:
        handler = _RecordCollector(self._records)
        self._handler = handler
        logging.getLogger("infra").addHandler(handler)

    def uninstall(self) -> None:
        if self._handler:
            logging.getLogger("infra").removeHandler(self._handler)

    @property
    def records(self) -> list[Any]:
        return self._records


class RemoteWrapper:
    """ExecutionWrapper: intercepts @mark.remote tests."""

    @property
    def marker(self) -> str:
        return "remote"

    def wrap(self, test_fn: Any, _: dict[str, Any]) -> Any:
        return test_fn()


def build_plugin(
    config: InfraConfig,
) -> tuple[InfraHostProvider, InfraLogBackend, RemoteWrapper]:
    """Construct protocol implementations from typed config."""
    return (
        InfraHostProvider(config),
        InfraLogBackend(),
        RemoteWrapper(),
    )
