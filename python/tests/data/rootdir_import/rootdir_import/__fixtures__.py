"""A declaration file that itself imports from the test tree.

This is the ordering test. `create_session` appends the rootdir before calling
`create_conftest_fixtures`, so this module's own import resolves. If the append
were moved later, this file would raise at session setup — before any test ran
— rather than failing a single assertion.
"""

from __future__ import annotations

import oxitest as oxi

from rootdir_import.helpers import make_user


@oxi.fixture(lifetime="function")
def seed_user() -> dict[str, str]:
    return make_user("seeded")
