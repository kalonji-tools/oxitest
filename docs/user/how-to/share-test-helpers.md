# Share test helpers

!!! abstract "How-to"
    Share utility functions and test doubles across test files using conftest helpers.

For background on how the namespace system works and why helpers live in conftest,
see [Conftest helpers](../explanation/conftest-helpers.md).

## Define helpers in conftest.py

Create a `Helpers()` instance and register functions with `@helpers.helper`:

```python
# tests/conftest.py
import oxitest
from oxitest import Helpers

fixtures = oxitest.Fixtures()
utils = Helpers()

@fixtures.fixture
def db():
    return connect()

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
```

The variable name (`utils`) becomes the namespace name.

## Use helpers in a test file

```python
# tests/test_users.py
from oxitest import helpers

def test_user_defaults():
    user = helpers.utils.make_user()
    assert user["name"] == "test"

def test_mailer():
    mailer = helpers.utils.FakeMailer()
    mailer.send("hello")
    assert len(mailer.sent) == 1
```

## Add helpers at different directory levels

```python
# tests/conftest.py
from oxitest import Helpers

common = Helpers()

@common.helper
def make_user(**overrides):
    defaults = {"name": "test", "email": "test@example.com"}
    return {**defaults, **overrides}
```

```python
# tests/integration/conftest.py
from oxitest import Helpers

integ = Helpers()

@integ.helper
def start_server(port=8080):
    ...
```

```python
# tests/integration/test_api.py
from oxitest import helpers

def test_api_endpoint():
    user = helpers.common.make_user(name="alice")
    server = helpers.integ.start_server()
    ...
```

## Choose a namespace name

The namespace is the variable name of the `Helpers()` instance:

```python
# tests/integration/conftest.py
integ = Helpers()  # namespace is "integ"
```

Tests use `helpers.integ.start_server()`.

You can also set the name explicitly:

```python
integ = Helpers(name="integ")
```

## See also

- [Use fixtures](use-fixtures.md) — fixture injection and conftest loading
- [Conftest helpers](../explanation/conftest-helpers.md) — design rationale for the helpers namespace
