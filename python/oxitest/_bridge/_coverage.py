"""Coverage integration for oxitest."""

from __future__ import annotations

__all__ = ["CovReportFormat", "CoveragePyProvider", "resolve_provider_standalone"]

import os
from enum import Enum
from typing import Any


class CovReportFormat(Enum):
    """Report format for coverage output."""

    TERM = "term"
    HTML = "html"
    XML = "xml"
    JSON = "json"
    NONE = "none"


class CoveragePyProvider:
    """Built-in coverage provider backed by coverage.py."""

    def __init__(self) -> None:
        self._cov: Any = None

    def start(self, config: dict) -> None:
        """Begin coverage collection."""
        try:
            import coverage as _coverage
        except ImportError:
            msg = "--cov requires coverage.py: pip install coverage"
            raise ImportError(msg) from None
        self._cov = _coverage.Coverage()
        self._cov.start()
        config_file = self._find_config_file()
        os.environ["COVERAGE_PROCESS_START"] = config_file

    def stop(self) -> None:
        """Stop collection, save data, combine parallel data files."""
        self._cov.stop()
        self._cov.save()
        self._cov.combine()

    def report(self, fmt: CovReportFormat) -> int:
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
