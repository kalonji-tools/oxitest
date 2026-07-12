"""Conftest for the share-test-helpers how-to guide."""

import oxitest
from oxitest import Helpers


def connect():
    """Stub — returns a mock DB connection."""
    return {"connected": True}


fixtures = oxitest.Fixtures()
utils = Helpers()


@fixtures.fixture
def db():
    return connect()


# fmt: off
# --8<-- [start:define-helpers]
@utils.helper
def make_user(**overrides):
    defaults = {"name": "test", "email": "test@example.com"}
    return {**defaults, **overrides}


@utils.helper
class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
# --8<-- [end:define-helpers]
# fmt: on
