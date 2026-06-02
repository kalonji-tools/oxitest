# Share test helpers

!!! abstract "How-to"
    Share utility functions and test doubles across test files using conftest helpers.

For background on how the namespace system works and why helpers live in conftest,
see [Conftest helpers](../explanation/conftest-helpers.md).

## Define helpers in conftest.py

Add public functions or classes to any `conftest.py`:

```python
# tests/conftest.py
import oxitest

fixtures = oxitest.Fixtures()

@fixtures.fixture
def db():
    return connect()

def make_user(**overrides):
    defaults = {"name": "test", "email": "test@example.com"}
    return {**defaults, **overrides}

class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
```

## Use helpers in a test file

```python
# tests/test_users.py
from conftest import helpers

def test_user_defaults():
    user = helpers.tests.make_user()
    assert user["name"] == "test"

def test_mailer():
    mailer = helpers.tests.FakeMailer()
    mailer.send("hello")
    assert len(mailer.sent) == 1
```

The sub-namespace (`tests`) is derived from the directory name.

## Add helpers at different directory levels

```python
# tests/conftest.py
def make_user(**overrides):
    defaults = {"name": "test", "email": "test@example.com"}
    return {**defaults, **overrides}
```

```python
# tests/integration/conftest.py
def start_server(port=8080):
    ...
```

```python
# tests/integration/test_api.py
from conftest import helpers

def test_api_endpoint():
    user = helpers.tests.make_user(name="alice")
    server = helpers.integration.start_server()
    ...
```

## Rename a namespace

```python
# tests/integration/conftest.py
__helpers_namespace__ = "integ"

def start_server(port=8080):
    ...
```

Tests now use `helpers.integ.start_server()`.

## Enable type checker support

```python
# tests/conftest.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge._helper_namespace import HelperNamespace
    helpers: HelperNamespace
```

## See also

- [Use fixtures](use-fixtures.md) — fixture injection and conftest loading
- [Conftest helpers](../explanation/conftest-helpers.md) — design rationale for the helpers namespace
