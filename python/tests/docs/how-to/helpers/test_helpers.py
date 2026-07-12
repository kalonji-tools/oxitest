"""Test functions for the share-test-helpers how-to guide."""

from oxitest import helpers


# fmt: off
# --8<-- [start:use-helpers]
def test_user_defaults():
    user = helpers.utils.make_user()
    assert user["name"] == "test", "make_user should provide default name"


def test_mailer():
    mailer = helpers.utils.FakeMailer()
    mailer.send("hello")
    assert len(mailer.sent) == 1, "FakeMailer should track sent messages"
# --8<-- [end:use-helpers]
# fmt: on
