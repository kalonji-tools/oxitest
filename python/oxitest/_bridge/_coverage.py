"""Coverage integration for oxitest."""

from __future__ import annotations

__all__ = [
    "_NULL_COVERAGE",
    "CovReportFormat",
    "CoveragePyProvider",
    "resolve_provider_standalone",
]

import os
import types
from enum import Enum
from typing import Any, Never

try:
    import coverage as _coverage
except ImportError:
    _coverage: types.ModuleType | None = None


class CovReportFormat(Enum):
    """Report format for coverage output.

    Passed to :meth:`CoverageProvider.report` to select the output format.
    ``NONE`` short-circuits the report step and returns 0 without emitting
    any output.

    See Also:
        - :meth:`oxitest.CoverageProvider.report` — the consumer.

    Examples:
        >>> from oxitest import CovReportFormat
        >>> CovReportFormat.HTML
        <CovReportFormat.HTML: 'html'>
        >>> CovReportFormat.TERM.value
        'term'
        >>> CovReportFormat("json")
        <CovReportFormat.JSON: 'json'>

    """

    TERM = "term"
    HTML = "html"
    XML = "xml"
    JSON = "json"
    NONE = "none"


class CoveragePyProvider:
    """Built-in coverage provider backed by coverage.py."""

    def __init__(self) -> None:
        self._cov: Any = None

    @property
    def cov(self) -> Any:
        """The underlying coverage.Coverage instance."""
        return self._cov

    @cov.setter
    def cov(self, value: Any) -> None:
        self._cov = value

    def start(self) -> None:
        """Begin coverage collection."""
        if _coverage is None:
            msg = "--cov requires coverage.py: pip install coverage"
            raise ImportError(msg)
        self._cov = _coverage.Coverage()
        self._cov.start()
        config_file = self._find_config_file()
        os.environ["COVERAGE_PROCESS_START"] = config_file

    def stop(self) -> None:
        """Stop collection, save data, combine parallel data files."""
        self._cov.stop()
        self._cov.save()
        self._cov.combine()

    def report(self, *, fmt: CovReportFormat) -> int:
        """Generate report in the given format."""
        match fmt:
            case CovReportFormat.TERM:
                return self._cov.report()
            case CovReportFormat.HTML:
                return self._cov.html_report()
            case CovReportFormat.XML:
                return self._cov.xml_report()
            case CovReportFormat.JSON:
                return self._cov.json_report()
            case CovReportFormat.NONE:
                return 0

    def _find_config_file(self) -> str:
        """Find the coverage config file path."""
        if hasattr(self._cov, "config") and hasattr(
            self._cov.config, "config_files_read"
        ):
            files = self._cov.config.config_files_read
            return files[0] if files else ""
        return ""


def resolve_provider_standalone() -> CoveragePyProvider:
    """Return the built-in coverage provider.

    Plugin-based resolution happens at a higher level; this function
    provides the default when no plugin overrides coverage.
    """
    return CoveragePyProvider()


class _NullCoverage:
    """Null-object stand-in when no plugin provides a coverage provider.

    Held on ``Plugin.coverage_provider`` and ``PluginRegistry.coverage_provider``
    as the canonical "no provider registered" value. Discovery filters this
    out by identity; any actual method call is a bug and raises
    ``AssertionError``.
    """

    def start(self) -> Never:
        msg = "null-object CoverageProvider method called — discovery filter is broken"
        raise AssertionError(msg)

    def stop(self) -> Never:
        msg = "null-object CoverageProvider method called — discovery filter is broken"
        raise AssertionError(msg)

    def report(self, *, fmt: CovReportFormat) -> Never:
        # Embed fmt in the message to satisfy ARG002 while keeping the
        # protocol-required kwarg name.
        msg = (
            f"null-object CoverageProvider method called (fmt={fmt})"
            " — discovery filter is broken"
        )
        raise AssertionError(msg)


_NULL_COVERAGE = _NullCoverage()
