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

Every `#[derive(FromPyObject)]` struct in `src/bridge.rs` has a Python counterpart
in `python/oxitest/_bridge/result.py`. The field names on both sides **must match
exactly** -- PyO3 extracts fields by attribute name, and a mismatch silently
produces a default value or a runtime error.

### TestResult

The primary result type for a single test execution.

**Python** (`python/oxitest/_bridge/result.py`):

```python
@dataclass
class TestResult:
    status: StatusKind
    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    no_message_lines: tuple[int, ...] = ()
    left: str = ""
    right: str = ""
    op: str = ""
    strict: bool = True
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()
    field_diffs: tuple[tuple[str, str, str], ...] = ()
```

**Rust** (`src/bridge.rs`):

```rust
#[derive(FromPyObject)]
struct TestResult {
    status: String,
    message: String,
    file: String,
    lineno: usize,
    source_line: String,
    no_message_lines: Vec<usize>,
    left: String,
    right: String,
    op: String,
    strict: bool,
    exc_type: String,
    frames: Vec<crate::worker_result::RawFrame>,
    field_diffs: Vec<(String, String, String)>,
}
```

PyO3's `FromPyObject` derive generates code that calls `ob.getattr("status")?.extract()?`
for each field. Python's `StatusKind` is a `StrEnum`, so it extracts as a `String` on
the Rust side. Tuple fields like `no_message_lines: tuple[int, ...]` extract as `Vec<usize>`.

Note that `exc_type` is marked `#[allow(dead_code)]` on the Rust side -- it exists
solely to keep the contract in sync. The value is used only in the Python layer.

### CollectedItem

Returned by `collect_module` for each discovered test function.

**Python** (`python/oxitest/_bridge/result.py`):

```python
@dataclass
class CollectedItem:
    fn_name: str
    lineno: int
    markers: tuple[str, ...]
    param_id: str | None
    param_values: tuple[tuple[str, str], ...]
    is_async: bool = False
    fixture_names: tuple[str, ...] = ()
    fixref_names: tuple[str, ...] = ()
```

**Rust** (`src/bridge.rs`):

```rust
#[derive(FromPyObject)]
struct CollectedItem {
    fn_name: String,
    lineno: usize,
    markers: Vec<String>,
    param_id: Option<String>,
    param_values: Vec<(String, String)>,
    is_async: bool,
    fixture_names: Vec<String>,
    fixref_names: Vec<String>,
}
```

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
    SINGLE_CASE_PARAMETRIZE = "single_case_parametrize"
    UNUSED_FIXTURE = "unused_fixture"

@dataclass
class CollectedViolation:
    node_id: str
    kind: ViolationKind
    detail: str
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

**Rust** (`src/worker_result.rs` + manual `FromPyObject` in `src/bridge.rs`):

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

1. **Add the field to the Python dataclass** in `python/oxitest/_bridge/result.py`.
   Give it a default value so existing code is not broken:

   ```python
   @dataclass
   class TestResult:
       # ... existing fields ...
       new_field: str = ""
   ```

2. **Add the field to the Rust `FromPyObject` struct** in `src/bridge.rs`, using
   the correct type mapping from the table above:

   ```rust
   #[derive(FromPyObject)]
   struct TestResult {
       // ... existing fields ...
       new_field: String,
   }
   ```

3. **Wire the field through** to where it is consumed. In bridge.rs, `TestResult`
   fields are mapped to `WorkerOutcome` variants in the `convert_test_result()`
   function. If the field affects the domain, add it to the appropriate
   `WorkerOutcome` variant and update the `From<WorkerOutcome> for TestOutcome`
   impl in `worker_result.rs`.

4. **Run both test suites** to verify sync:

   ```bash
   just test-rust   # Rust unit tests, including convert_tests in bridge.rs
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
  string value in a `match` expression (see `convert_test_result()`).

## Testing the contract

### What tests exist

- **`convert_tests` in `src/bridge.rs`**: Unit tests that construct a Rust
  `TestResult` directly (no Python) and verify `convert_test_result()` maps every
  status string to the correct `WorkerOutcome` variant. These catch logic errors
  in the status dispatch.

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
