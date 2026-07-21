"""Plugin-entry sum type — leaf module for break-cycle purposes.

Kept minimal so ``_async_backend.py`` (imported early by ``oxitest.__init__``)
can reference :class:`ActivatedPluginEntry` without triggering the
``_async_backend → plugin_loader → oxitest.plugin → _async_backend`` cycle.
"""

from __future__ import annotations

__all__ = ["ActivatedPluginEntry", "DeferredPluginEntry", "PluginEntry"]

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest.plugin import Plugin


@dataclass(frozen=True, slots=True)
class DeferredPluginEntry:
    """A plugin declared in config but not yet imported.

    Two paths land here: (1) plugins declaring only lazy protocols, deferred
    until first use; (2) plugins with a CLI extension, awaiting typed-config
    activation from :func:`activate_deferred_plugins`.
    """

    module_name: str
    declared_protocols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivatedPluginEntry:
    """A loaded plugin whose :class:`~oxitest.plugin.Plugin` instance is available."""

    module_name: str
    plugin: Plugin
    declared_protocols: tuple[str, ...] = ()


PluginEntry = DeferredPluginEntry | ActivatedPluginEntry
