# PyO3 Bridge Contract

The bridge is the narrow boundary between Rust orchestration and Python execution.
It lives in `src/bridge.rs` on the Rust side and `python/oxitest/_bridge/result.py`
on the Python side.

> **User guide:** See [Using Fixtures](../../site/how-to/use-fixtures/) for practical usage and examples of the fixture system described here.

## Call directions

Communication flows in **one direction per channel**:

| Direction | Mechanism | Examples |
|-----------|-----------|---------|
| Rust -> Python | PyO3 function calls | `collect_module`, `run_test`, `FixtureSession` lifecycle |
| Python -> Rust | Return values (data) | `TestResult`, `CollectedItem`, `RawViolation` |

Python never calls into Rust during normal execution. Instead, Python functions
return plain data objects, and Rust extracts their fields via PyO3's `FromPyObject`
trait.

## Data types that must stay in sync

Most data types crossing the bridge have a Python counterpart in
`python/oxitest/_bridge/result.py`. For types using `#[derive(FromPyObject)]`
(e.g. `CollectedItem`, `RawViolation`), field names on both sides **must match
exactly** — PyO3 extracts fields by attribute name, and a mismatch silently
produces a default value or a runtime error. `TestResult` is an exception: it
uses a manual `extract_outcome()` function rather than a derived struct (see
below).

### TestResult

The primary result type for a single test execution.

**Python** (`python/oxitest/_bridge/result.py`):

`TestResult` is a **type alias** over per-outcome frozen dataclasses — there is no
single unified `TestResult` class. Each outcome kind carries only the fields it needs:

```python
@dataclass(frozen=True, slots=True)
class PassedResult:
    no_message_lines: tuple[int, ...] = ()

@dataclass(frozen=True, slots=True)
class FailedResult:
    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    no_message_lines: tuple[int, ...] = ()
    left: str = ""
    right: str = ""
    op: str = ""
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()
    field_diffs: tuple[tuple[str, str, str], ...] = ()

@dataclass(frozen=True, slots=True)
class ErrorResult:
    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()

@dataclass(frozen=True, slots=True)
class SkippedResult:
    message: str = ""

@dataclass(frozen=True, slots=True)
class WarnedResult:
    message: str = ""
    no_message_lines: tuple[int, ...] = ()

@dataclass(frozen=True, slots=True)
class XFailedResult:
    message: str = ""

@dataclass(frozen=True, slots=True)
class XPassedResult:
    strict: bool = True

@dataclass(frozen=True, slots=True)
class TimeoutResult:
    message: str = ""

TestResult = (
    PassedResult | FailedResult | ErrorResult | SkippedResult
    | WarnedResult | XFailedResult | XPassedResult | TimeoutResult
)
```

Every per-outcome class exposes a `status` property that returns the matching
`StatusKind` value (e.g. `PassedResult.status` returns `StatusKind.PASSED`).
The `to_wire()` method serialises the result to a JSON-compatible dict for the
worker protocol.

**Rust** (`src/bridge.rs`):

There is **no** `#[derive(FromPyObject)] struct TestResult` on the Rust side.
Instead, `extract_outcome()` reads the `status` attribute first, then extracts
only the fields that are relevant to that outcome kind. This avoids redundant
`getattr` calls for the common `passed` path:

```rust
fn extract_outcome(py_result: &Bound<'_, PyAny>) -> PyResult<TestOutcome> {
    use crate::worker_result::RawOutcome;

    let status: String = py_result.getattr("status")?.extract()?;

    match status.as_str() {
        "passed" => {
            let no_message_lines: Vec<usize> =
                py_result.getattr("no_message_lines")?.extract()?;
            Ok(RawOutcome::Passed { no_message_lines }.into_test_outcome())
        }
        "failed" => {
            let message: String = py_result.getattr("message")?.extract()?;
            let file: String    = py_result.getattr("file")?.extract()?;
            let lineno: usize   = py_result.getattr("lineno")?.extract()?;
            // ... more fields ...
            Ok(RawOutcome::Failed { message, file: file.into(), lineno: LineNo::new(lineno), /* … */ }
                .into_test_outcome())
        }
        // "skipped", "xfailed", "xpassed", "timeout", "warned" — each arm
        // extracts only its own fields.
        _ => { /* treated as ErrorResult */ Ok(RawOutcome::Error { /* … */ }.into_test_outcome()) }
    }
}
```

`extract_outcome()` builds a [`RawOutcome`](../worker_result/convert.rs) variant and
calls `into_test_outcome()` — the single conversion path shared with the JSON worker.
Passed tests (the common case) go from 13 `getattr` calls down to 2.

### CollectedItem

Returned by `collect_module` for each discovered test function.

**Python** (`python/oxitest/_bridge/result.py`):

```python
@dataclass(frozen=True, slots=True)
class CollectedItem:
    fn_name: str
    lineno: int
    markers: tuple[str, ...]
    kind: TestKind                                  # Parametrized(param_id) | Solitary
    param_values: tuple[tuple[str, str], ...]
    is_async: bool = False
    fixture_deps: tuple[tuple[str, str], ...] = ()  # (qualifier, type_name)
    fixref_deps: tuple[tuple[str, str], ...] = ()   # (qualifier, type_name)

    @property
    def param_id(self) -> str | None:
        # Wire adapter for the Rust FromPyObject extraction below.
        return self.kind.to_wire()
```

`kind: TestKind` (ADR-0007 Rule 2) is the sum-type source of truth; `param_id`
is exposed as a `@property` so the Rust `#[derive(FromPyObject)]` extraction —
which reads by attribute name — sees the same `Option<String>` shape as before.
`scripts/check_bridge_sync.py` recognises the property bridge via its
`PROPERTY_BRIDGES` table.

**Rust** (`src/bridge.rs`):

```rust
#[derive(FromPyObject)]
struct CollectedItem {
    fn_name: String,
    lineno: usize,
    markers: Vec<String>,
    param_id: Option<String>,  // reads the @property adapter, not a field
    param_values: Vec<(String, String)>,
    is_async: bool,
    fixture_deps: Vec<(String, String)>,  // (qualifier, binding_type_name)
    fixref_deps: Vec<(String, String)>,
}
```

Each `fixture_deps` entry is a `(qualifier, type_name)` pair — the parameter name
(used as a qualifier for disambiguation) and the binding type name (the `T` from
`Fixture[T]`). This replaced the earlier `fixture_names: Vec<String>` which only
carried parameter names and excluded builtins.

After extraction, Rust converts each `CollectedItem` into a `TestItem` in
`collect_module_with_session_obj()`, computing the `NodeId` and copying fields
into the domain struct.

### RawViolation / CollectedViolation

Strict-mode violations detected at collection time.

**Python** (`python/oxitest/_bridge/result.py`):

```python
class ViolationKind(StrEnum):
    BARE_ASSERT = "bare_assert"
    DICT_PARAMETRIZE = "dict_parametrize"
    INVALID_MODULE_MARK = "invalid_module_mark"
    MISSING_MARK_REASON = "missing_mark_reason"
    MISSING_RETURN_ANNOTATION = "missing_return_annotation"
    REGISTRAR_IN_TEST_MODULE = "registrar_in_test_module"
    SINGLE_CASE_PARAMETRIZE = "single_case_parametrize"
    BROAD_FIXTURE_TYPE = "broad_fixture_type"
    UNUSED_FIXTURE = "unused_fixture"

@dataclass(frozen=True, slots=True)
class CollectedViolation:
    node_id: str
    kind: ViolationKind
    detail: str  # kind-specific payload; empty string when unused
```

**Rust** (`src/bridge.rs`):

```rust
#[derive(pyo3::FromPyObject, Debug, Clone)]
pub(crate) struct RawViolation {
    pub node_id: String,
    pub kind: ViolationKind,
    pub detail: String,
}
```

`ViolationKind` has a manual `FromPyObject` implementation that matches the Python
`StrEnum` values by string. Unknown values map to `ViolationKind::Unknown`.

### RawFrame

Traceback frames, used by both the PyO3 bridge path and the JSON worker path.

**Python** (`python/oxitest/_bridge/result.py`):

```python
@dataclass
class Frame:
    file: str
    lineno: int
    name: str
    line: str
    locals: tuple[tuple[str, str], ...] = ()
```

**Rust** (`src/worker_result/wire.rs` + manual `FromPyObject` in `src/bridge.rs`):

```rust
#[derive(Debug, Clone, serde::Deserialize)]
pub(crate) struct RawFrame {
    pub file: String,
    pub lineno: u64,
    pub name: String,
    pub line: String,
    #[serde(default)]
    pub locals: Vec<(String, String)>,
}
```

`RawFrame` is unique in that it serves both paths: `serde::Deserialize` for the
worker JSON protocol, and a manual `FromPyObject` impl in `bridge.rs` for the
in-process PyO3 path. Both paths convert to the domain `Frame` type via
`impl From<RawFrame> for Frame`.

## Type mapping reference

| Python type | Rust type | Notes |
|-------------|-----------|-------|
| `str` | `String` | |
| `int` | `usize` or `u64` | `lineno` uses `usize` in bridge, `u64` in wire |
| `bool` | `bool` | |
| `str \| None` | `Option<String>` | PyO3 extracts `None` as `Option::None` |
| `tuple[str, ...]` | `Vec<String>` | Tuples and lists both extract as `Vec` |
| `tuple[tuple[str, str], ...]` | `Vec<(String, String)>` | Nested tuples become `Vec` of tuples |
| `StrEnum` | `String` | `StrEnum` is a `str` subclass, extracts naturally |

## How to add a field to the bridge

Adding a new field requires synchronized changes on both sides. The steps:

1. **Add the field to the appropriate per-outcome Python dataclass** in
   `python/oxitest/_bridge/result.py`. Give it a default value so existing
   code is not broken. Add it only to the outcome kinds that need it —
   `TestResult` is a type alias, not a single class:

   ```python
   @dataclass(frozen=True, slots=True)
   class FailedResult:
       # ... existing fields ...
       new_field: str = ""
   ```

2. **Extract the field in `extract_outcome()`** in `src/bridge.rs`, inside the
   `match` arm for the relevant outcome status. There is no `FromPyObject` struct
   to update — extraction is done imperatively:

   ```rust
   "failed" => {
       // ... existing extractions ...
       let new_field: String = py_result.getattr("new_field")?.extract()?;
       Ok(RawOutcome::Failed { /* …, new_field */ }.into_test_outcome())
   }
   ```

3. **Wire the field through** to where it is consumed. In `bridge.rs`,
   `extract_outcome()` maps Python fields to `RawOutcome` variants (defined in
   `worker_result/convert.rs`). If the field affects the domain, add it to the
   appropriate `RawOutcome` variant and update `into_test_outcome()`.

4. **Run both test suites** to verify sync:

   ```bash
   just test-rust   # Rust unit tests, including raw_outcome_tests in worker_result/tests.rs
   just test        # Python integration tests
   ```

### Common pitfalls

- **Forgetting a field on one side.** PyO3 will raise a Python `AttributeError`
  at runtime if the Rust struct expects a field that doesn't exist on the Python
  object. If the Python object has a field not in the Rust struct, it is silently
  ignored.

- **Type mismatches.** A Python `int` that is too large for `usize` will cause an
  `OverflowError`. Use `u64` if the value might exceed platform `usize`.

- **Optional vs required.** If the Python field has a default value, the Rust field
  can use the base type (e.g. `String` for `str = ""`). Use `Option<T>` only when
  the Python value can be `None`.

- **Field name renames.** If the Python attribute name differs from the Rust field
  name, use `#[pyo3(attribute("python_name"))]` on the Rust field. Currently no
  fields use this, but it is available if needed.

- **`StrEnum` vs plain `str`.** Python `StrEnum` values are `str` subclasses, so
  they extract as `String` without any special handling. The Rust side matches the
  string value in a `match` expression (see `extract_outcome()` in `bridge.rs`).

## Testing the contract

### What tests exist

- **`raw_outcome_tests` in `src/worker_result/tests.rs`**: Unit tests that
  construct `RawOutcome` variants directly and verify `into_test_outcome()` maps
  each to the correct `TestOutcome`. These catch logic errors in the unified
  conversion path shared by both PyO3 and JSON worker callers.

- **Python integration tests** (`python/tests/`): End-to-end tests that exercise
  `collect_module` and `run_test` through the full PyO3 path. These catch field
  sync issues because PyO3 will fail to extract if a field is missing or has the
  wrong type.

### What errors look like when types are out of sync

If a field is **missing on the Python side** but expected on the Rust side, you see:

```
pyo3::exceptions::PyAttributeError: 'TestResult' object has no attribute 'new_field'
```

If a field has the **wrong type** (e.g. Python returns `str` but Rust expects `bool`):

```
pyo3::exceptions::PyTypeError: 'str' object cannot be interpreted as a boolean
```

If a field exists on **Python but not Rust**, there is no error -- the field is
silently ignored. This is safe but wasteful; clean it up when noticed.

## See also

- [Worker Protocol](worker-protocol.md) -- the JSON wire format used by parallel workers
- [Architecture Overview](architecture.md) -- module map and where bridge code lives
