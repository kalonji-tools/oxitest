# Debug tests

!!! abstract "How-to"
    Drop into an interactive debugger when a test fails.

## Post-mortem debugging

When a test fails and you want to inspect the failure interactively:

```console
$ oxitest --debug tests/test_math.py
```

oxitest runs the test serially, and when it hits a failure, drops you into
Python's `pdb` debugger at the point of the exception:

```
── DEBUG tests/test_math.py::test_add ──────────────────
AssertionError: expected 3, got 5
Entering debugger (type 'h' for help, 'q' to quit)
> /home/user/project/tests/test_math.py(4)test_add()
(Pdb)
```

From here you can inspect local variables, walk the call stack, and
understand exactly what went wrong.

`--debug` implies `--serial` (single process), `--maxfail 1` (stop on
first failure), and `--show-internals` (full traceback after the session).
You can suppress internal frames with an explicit `--tb`:

```console
$ oxitest --debug --tb=detail tests/test_math.py
```

## Stepping through every test

To drop into the debugger at the start of every test:

```console
$ oxitest --debug=always tests/test_math.py
```

oxitest pauses before each test function runs, showing:

```
── TRACE tests/test_math.py::test_add ──────────────────
Stepping into test (type 'c' to run, 'q' to quit)
(Pdb)
```

Type `c` to continue into the test, or `q` to stop the run. If the test
fails, you also get a post-mortem debugger session at the failure point.

Unlike `--debug` (post-mortem), `always` mode does not imply `--maxfail 1`.
You can combine it with `-x` to stop on first failure:

```console
$ oxitest --debug=always -x tests/
```

Or let it run all tests with a debugger pause before each one.

## Using breakpoints

Under `--debug`, `breakpoint()` calls in your test code work normally.
This lets you set proactive breakpoints before a failure occurs:

```python
def test_complex_calculation():
    data = prepare_data()
    breakpoint()  # pause here to inspect `data`
    result = calculate(data)
    assert result == expected
```

Run with `--debug` to ensure serial mode (which keeps stdin connected):

```console
$ oxitest --debug tests/test_complex.py
```

## Essential pdb commands

Once in the debugger, these commands help you navigate:

| Command | Short | What it does |
|---------|-------|-------------|
| `help` | `h` | Show available commands |
| `print expr` | `p expr` | Evaluate and print an expression |
| `pp expr` | | Pretty-print an expression |
| `list` | `l` | Show source code around the current line |
| `where` | `w` | Print the full call stack |
| `up` | `u` | Move one frame up in the call stack |
| `down` | `d` | Move one frame down in the call stack |
| `next` | `n` | Execute the next line (step over) |
| `step` | `s` | Step into a function call |
| `continue` | `c` | Continue execution (exits debugger, test still fails) |
| `quit` | `q` | Quit the debugger and stop the test run |

## Common workflows

**"A test fails and I don't understand why"**

```console
$ oxitest --debug tests/test_failing.py
```

In the debugger, use `p variable_name` to inspect locals and `w` to see
the call stack.

**"I want to step through a specific test"**

Add `breakpoint()` at the line you want to inspect, then run:

```console
$ oxitest --debug tests/test_specific.py -k test_name
```

**"I want to see all variables at the failure point"**

In the debugger, type `locals()` or use `pp locals()` for a formatted view.
