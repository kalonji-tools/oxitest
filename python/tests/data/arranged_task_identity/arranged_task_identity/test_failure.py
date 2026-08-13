"""An arranged async fixture whose setup raises.

The log helper is duplicated here for the reason ``test_order.py`` gives.
"""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    path = os.environ["TASK_IDENTITY_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.arrange("failing")
async def test_arranged_setup_failure() -> None:
    _record("FAILURE-BODY RAN")
