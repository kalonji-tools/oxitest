"""Process exit codes returned by oxitest runs."""

from enum import IntEnum

__all__ = ["ExitCode"]


class ExitCode(IntEnum):
    """Process exit codes returned by oxitest runs.

    Inherits from :class:`int`, so ``sys.exit(ExitCode.SUCCESS)`` works
    directly and comparisons like ``rc == ExitCode.FAILURE`` are valid.

    See Also:
        - ``python -m oxitest`` — CLI entry point that maps its return
          value to one of these codes via ``sys.exit``.

    Examples:
        >>> from oxitest import ExitCode
        >>> ExitCode.SUCCESS
        <ExitCode.SUCCESS: 0>
        >>> int(ExitCode.FAILURE)
        1
        >>> ExitCode.INTERRUPTED == 2
        True
        >>> ExitCode(4).name
        'USAGE_ERROR'

    """

    SUCCESS = 0
    """All tests passed (or were skipped / xfailed)."""

    FAILURE = 1
    """At least one hard failure (failed, errored, timed out, strict-xpassed)."""

    INTERRUPTED = 2
    """The run was interrupted (e.g. Ctrl-C / SIGINT)."""

    COLLECT_ERROR = 3
    """One or more collection errors (import failures, syntax errors)."""

    USAGE_ERROR = 4
    """Invalid CLI arguments or conflicting flags."""
