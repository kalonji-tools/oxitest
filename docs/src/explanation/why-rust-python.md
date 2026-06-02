# Why Rust + Python?

!!! abstract "Explanation"
    The reasoning behind oxitest's hybrid architecture and the trade-offs that come with it.

## The problem with a pure-Python test runner

Python is the natural language for a Python test runner. pytest, unittest, and every other
mainstream option are written entirely in Python. That works, but it comes with a performance
ceiling set by the language itself.

The slowness appears in three places.

**Collection overhead.** Before a single test runs, the runner must find every test file,
import every module, and inspect every name inside it. For a large project, this means
hundreds of `import` calls. Each triggers the interpreter to read bytes from disk, compile
them to bytecode, execute the module top-level, and register the result in `sys.modules`.
Python's `os.walk` adds interpreted overhead on every directory entry. The entire collection
phase is CPU-bound interpreted work, and it completes before the first assertion fires.

**The GIL.** Even when a runner tries to parallelize collection or result aggregation,
CPython's Global Interpreter Lock prevents true parallelism for CPU-bound Python code. Two
threads cannot execute Python bytecode simultaneously.

**Formatting and I/O.** Writing colored terminal output, reading TOML config files, resolving
glob patterns for test selection — none of this requires Python semantics, yet a pure-Python
runner pays Python's per-operation cost for all of it.

## What test execution genuinely needs from Python

Some things cannot be moved to Rust without breaking real test code.

Test functions import the modules they test. Those modules may register hooks in `sys.modules`,
mutate global state, or depend on other Python packages. The only runtime that can correctly
execute them is the Python interpreter.

Fixtures depend on Python's introspection — `inspect.signature`, generator semantics for setup
and teardown, and exception chaining. Replicating this in Rust would mean re-implementing a
significant slice of CPython.

Exception handling is similarly Python-native. When a test raises `AssertionError`, the
traceback is a live Python object with frames, locals, and source references. Capturing it
meaningfully requires being inside the Python runtime.

**The rule oxitest follows:** anything that touches user-written Python code stays in Python.

## What can safely run in Rust

Everything that does not touch user-written Python code is a candidate for Rust.

**File discovery.** Walking a directory tree and matching filenames against glob patterns is
pure I/O plus string matching. The `walkdir` crate handles this with a tight loop that avoids
allocation on the hot path, and `globset` compiles patterns once and matches in nanoseconds.

**Config parsing.** Reading `pyproject.toml` and deserializing it into a typed struct is a
one-time cost. The `toml` crate does this at native speed; `serde` eliminates runtime type
dispatch.

**Progress and output.** The `indicatif` crate draws progress bars and the `console` crate
handles ANSI colors. Both operate on bytes and write directly to a file descriptor. There is
no interpreter in the loop.

**Concurrency coordination.** Rust's ownership model and `Send`/`Sync` guarantees make it
straightforward to coordinate work across processes without data races. Parallel execution
spawns subprocess workers (`python -m oxitest._bridge.worker`) that communicate with the Rust
scheduler over stdio JSON.

**CLI parsing.** `clap` parses arguments at native speed and generates help text from struct
attributes and doc comments.

## How PyO3 bridges the two worlds

PyO3 is the crate that lets Rust call into Python and Python call into Rust.

oxitest compiles to a Python extension module. When `oxitest` is invoked, Python calls the
Rust `run()` function, passing the raw command-line argument strings. Rust parses them with
`clap` and then drives the whole lifecycle — discovery, filtering, reporting — but for each
test it calls back into Python through a thin executor layer.

That call returns a result dataclass. On the Rust side, `TestResult` implements
`#[derive(FromPyObject)]`, which lets PyO3 extract the fields from the Python object without
going through a string-serialization round-trip. The data crosses the boundary as native
Python objects; Rust reads their fields directly from CPython's memory layout.

**The bridge is narrow by design.** A small, stable interface between the two languages means
the two sides can evolve independently and the failure surface stays small.

## The honest trade-off

The hybrid architecture adds build complexity that a pure-Python project avoids. oxitest
requires a Rust toolchain at build time. The compiled extension is platform-specific, so
wheels must be built for each target. The PyO3 version must stay compatible with the CPython
version the user has installed.

In return, every part of the runner that does not execute test code runs at native speed.
Collection, config parsing, filtering, output formatting, and scheduling all happen without
touching the Python interpreter.

The parallel execution model extends this further: the work-stealing scheduler distributes
test groups across subprocess workers, keeping all cores busy. The GIL inside each worker
affects only that worker's test execution, not the coordination layer. For large suites, the
speed advantage compounds with suite size.

The design choice remains a bet: the things that can be made fast in Rust are worth the
complexity, and the things that must stay in Python are few enough that the boundary stays
manageable.

## See also

- [Architecture](../contributing/architecture.md) — how the Rust and Python layers are structured
- [Performance](performance.md) — where the speed comes from
- [Worker protocol](worker-protocol.md) — how Rust and Python communicate in parallel mode
