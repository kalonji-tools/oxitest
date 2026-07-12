"""Tested examples for the exceptions reference page."""

import oxitest


# fmt: off
# --8<-- [start:shared-mutation-error]
def test_mutation(config: oxitest.Fixture[dict[str, str]]) -> None:
    with oxitest.raises(oxitest.SharedFixtureMutationError):
        config["env"] = "prod"
# --8<-- [end:shared-mutation-error]
# fmt: on
