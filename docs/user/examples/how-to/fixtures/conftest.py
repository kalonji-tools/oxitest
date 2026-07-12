"""Fixture definitions for the use-fixtures how-to guide.

Provides all fixture examples with typed stubs for disambiguation.
"""

import tempfile
from collections.abc import Generator
from pathlib import Path

import oxitest
from oxitest import Fixture, TestContext


class Connection:
    """Stub DB connection for doc examples."""

    def execute(self, sql: str) -> None: ...
    def close(self) -> None: ...
    def query(self, sql: str, *args: object) -> dict:
        return {"name": "Alice"}

    def export(self) -> str:
        return '{"data": []}'


class User:
    """Stub user for doc examples."""

    def __init__(self, *, id: int, name: str) -> None:
        self.id = id
        self.name = name


class HttpClient:
    """Stub HTTP client for doc examples."""

    class _Response:
        def json(self) -> dict:
            return {"id": 1}

    def post(self, url: str, *, json: dict | None = None) -> _Response:
        return self._Response()


def connect(url: str) -> Connection:
    """Stub — returns a Connection."""
    return Connection()


def load_config(path: str) -> dict:
    """Stub — returns a sample config dict."""
    return {"db_url": "sqlite:///:memory:", "debug": True}


# fmt: off
# --8<-- [start:declare-registry]
fx = oxitest.Fixtures()
# --8<-- [end:declare-registry]

# --8<-- [start:simple-fixture]
@fx.fixture
def sample_data() -> list[int]:
    return [1, 2, 3, 4, 5]
# --8<-- [end:simple-fixture]

# --8<-- [start:yield-fixture]
@fx.fixture
def temp_db() -> Generator[Connection, None, None]:
    conn = connect("sqlite:///:memory:")
    conn.execute("CREATE TABLE t (id INTEGER)")
    yield conn
    conn.close()
# --8<-- [end:yield-fixture]

# --8<-- [start:imperative-teardown]
@fx.fixture
def managed_file(ctx: TestContext) -> Path:
    fd, name = tempfile.mkstemp()
    import os
    os.close(fd)
    path = Path(name)
    path.write_text("hello")
    ctx.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path
# --8<-- [end:imperative-teardown]

# --8<-- [start:fixture-depends-on-fixture]
@fx.fixture
def user(db: Fixture[Connection]) -> User:
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    return User(id=1, name="Alice")
# --8<-- [end:fixture-depends-on-fixture]

# --8<-- [start:shared-fixture]
@fx.fixture(shared=True)
def app_config() -> dict:
    return load_config("config.yaml")
# --8<-- [end:shared-fixture]

# --8<-- [start:autouse-fixture]
@fx.fixture(autouse=True)
def reset_database(db: Fixture[Connection]) -> Generator[None, None, None]:
    yield
    db.execute("DELETE FROM users")
# --8<-- [end:autouse-fixture]
# fmt: on


# Namespace fixtures for multi-namespace example
db = oxitest.Fixtures()
http = oxitest.Fixtures()


# fmt: off
# --8<-- [start:namespace-fixtures]
class NamespacedConnection(Connection):
    """Distinct type for namespace fixture to avoid ambiguity with temp_db."""


@db.fixture
def conn() -> NamespacedConnection:
    return NamespacedConnection()


@http.fixture
def client() -> HttpClient:
    return HttpClient()
# --8<-- [end:namespace-fixtures]
# fmt: on
