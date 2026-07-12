"""Tested examples for the use-class-based-tests how-to guide."""

import oxitest


# fmt: off
# --8<-- [start:basic-class]
class TestStack:
    def test_push(self):
        stack = []
        stack.append(1)
        assert stack == [1], "push should add to stack"

    def test_pop(self):
        stack = [1, 2]
        assert stack.pop() == 2, "pop should remove last item"
        assert stack == [1], "stack should have remaining items"
# --8<-- [end:basic-class]

# --8<-- [start:class-marks]
@oxitest.mark.slow
class TestExpensive:
    def test_heavy_computation(self):
        assert sum(range(1000)) == 499500, "heavy computation should complete"

    @oxitest.mark.skip(reason="not yet implemented")
    def test_future_feature(self):
        ...
# --8<-- [end:class-marks]
# fmt: on
