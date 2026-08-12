"""Tests for the stage-8 finding poster in ``post_review_findings.py``.

Anchoring is narrower than GitHub's documentation suggests, and every constraint
below was measured against a live pull request rather than read (#2007):

* a line outside every diff hunk is refused with 422
* ``subject_type`` is rejected inside a review and works only standalone
* a file the branch does not touch cannot be anchored at all
* a file whose diff carries no ``patch`` — binary, or a very large diff — can be
  anchored only at file level

Validation exists so those arrive as one refusal naming every problem, rather
than as separate 422s discovered halfway through posting, with some threads
already created and a review body claiming findings that do not exist.

The hunk-header case deserves its own note. The header's ``+start,count`` range
*is* the commentable set: git's default context is already inside ``count``.
Widening it by the context size again yields line numbers that GitHub refuses,
and the resulting failure would look like a GitHub bug rather than an
arithmetic one.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "post_review_findings.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/post_review_findings.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``.
    The ``sys.modules`` registration matches the sibling loaders and costs
    nothing if the module gains a dataclass later.
    """
    spec = importlib.util.spec_from_file_location(
        "post_review_findings_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# A two-hunk patch in the shape GitHub returns: new-file lines 14-20 and 22-27
# are rendered and therefore commentable; 13, 21 and 28 are not.
_PATCH = (
    "@@ -14,7 +14,7 @@ def _clear(self):\n"
    "     def _inject(self, thread_id, setter=None):\n"
    "-        setter = setter or _set_async_exc\n"
    "+        pass\n"
    "         secs = self.timeout_secs\n"
    "@@ -22,3 +22,6 @@ def _inject(self):\n"
    "     def __exit__(self, *exc):\n"
    "+\n"
    "+def _set_async_exc(tid, secs):\n"
    "+    return 1\n"
)


def _spec(findings: list[dict], *, slug: object = "improve") -> dict:
    """A pass spec carrying the given findings."""
    return {
        "slug": slug,
        "pass": "/improve branch",
        "narrative": "scoped three-dot against the merge-base",
        "findings": findings,
    }


# ── Commentable lines ────────────────────────────────────────────────────────


def test_the_hunk_header_range_is_the_commentable_set() -> None:
    """Git's default context is already inside the header's count."""
    # Arrange
    module = _load_script_module()

    # Act
    lines = module.commentable_lines(_PATCH)

    # Assert
    assert lines == set(range(14, 21)) | set(range(22, 28)), (
        "the header's +start,count spans exactly the lines GitHub renders, so the "
        "range is used as-is; any other set posts comments the API refuses"
    )
    assert 13 not in lines, (
        "line 13 sits before the first hunk and is not rendered in the diff; "
        "widening the range by the context size would wrongly include it"
    )
    assert 21 not in lines, (
        "line 21 falls between the two hunks — the exact case that returned 422 "
        "'line could not be resolved' when measured against a live pull request"
    )
    assert 28 not in lines, (
        "line 28 sits past the last hunk; including it would make validation pass "
        "and the post fail, which is the failure mode validation exists to remove"
    )


def test_a_patch_with_no_hunks_yields_no_commentable_lines() -> None:
    """An empty patch anchors nothing, rather than raising."""
    # Arrange
    module = _load_script_module()

    # Act
    lines = module.commentable_lines("")

    # Assert
    assert lines == set(), (
        "a file with no rendered hunks has nowhere to hang an inline comment; "
        "returning empty lets validation refuse cleanly instead of crashing"
    )


# ── Diff index ───────────────────────────────────────────────────────────────


def test_a_line_inside_a_hunk_is_anchorable() -> None:
    """Both hunks are commentable, not just the first."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})

    # Act / Assert
    assert diff.can_anchor_line("x.py", 17) is True, (
        "line 17 is a changed line in the first hunk — the ordinary case, and if "
        "this fails no finding can be anchored at all"
    )
    assert diff.can_anchor_line("x.py", 27) is True, (
        "line 27 is in the second hunk; a parser that stops after the first header "
        "silently refuses every finding below it"
    )


def test_a_line_between_hunks_is_not_anchorable() -> None:
    """Unrendered lines are refused before posting, not after."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})

    # Act / Assert
    assert diff.can_anchor_line("x.py", 21) is False, (
        "GitHub returns 422 for a line it did not render; catching it here turns a "
        "mid-post failure into a refusal that names the problem"
    )


def test_a_file_with_no_patch_is_anchorable_only_at_file_level() -> None:
    """Binary files and very large diffs come back with no ``patch`` at all.

    Measured: a 60,000-line text file and a binary file both returned no
    ``patch`` field, both 422'd on an inline comment, and both accepted a
    file-level one. Treating absent-patch as "no constraint" emits the very 422
    validation exists to prevent; treating it as "not in the diff" refuses a
    finding that could legitimately be posted at file level.
    """
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"blob.bin": None})

    # Act / Assert
    assert diff.is_in_diff("blob.bin") is True, (
        "the file is genuinely part of the diff, so a file-level thread on it is "
        "valid and must not be refused as an untouched path"
    )
    assert diff.has_patch("blob.bin") is False, (
        "no patch means no rendered lines, which is what forces the file-level "
        "fallback rather than an inline anchor"
    )
    assert diff.can_anchor_line("blob.bin", 1) is False, (
        "an inline anchor on a patch-less file is refused with 422; validation "
        "must reject it rather than discover it at post time"
    )


def test_a_path_outside_the_diff_is_not_in_the_diff() -> None:
    """Untouched files cannot carry a thread by any API."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})

    # Act / Assert
    assert diff.is_in_diff("other.py") is False, (
        "a file the branch never touched returned 422 'path could not be "
        "resolved'; those findings become issues instead of threads"
    )


# ── Markers ──────────────────────────────────────────────────────────────────


def test_the_marker_is_namespaced_by_pass_slug() -> None:
    """Two passes each emit a ``#1``; an un-namespaced marker collides.

    The prototype used a bare ``Finding N`` and both passes produced a
    ``Finding 1``, so a disposition would have resolved whichever thread was
    found first. The collision was invisible until the gate was run.
    """
    # Arrange
    module = _load_script_module()

    # Act
    ponytail = module.marker("ponytail", 1)
    improve = module.marker("improve", 1)

    # Assert
    assert ponytail == "ponytail #1", (
        "the marker is what a disposition looks a thread up by, so its format is "
        "a contract between the poster and the disposer"
    )
    assert ponytail != improve, (
        "without the slug both passes' first findings share a marker and a "
        "disposition silently lands on the wrong thread"
    )


# ── Validation ───────────────────────────────────────────────────────────────


def test_a_well_formed_spec_is_accepted() -> None:
    """All three finding shapes pass validation together."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec(
        [
            {"id": 1, "title": "t", "body": "b", "path": "x.py", "line": 17},
            {"id": 2, "title": "t", "body": "b", "path": "x.py"},
            {"id": 3, "title": "t", "body": "b", "issue": 2011},
        ]
    )

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert problems == [], (
        "inline, file-level and off-diff findings are all legitimate shapes; "
        f"rejecting any of them blocks a whole pass from posting, got {problems}"
    )


def test_validation_rejects_a_finding_in_an_untouched_file() -> None:
    """The path cannot be anchored, so the finding needs an issue instead."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": 1, "title": "t", "body": "b", "path": "other.py", "line": 3}])

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"exactly one thing is wrong with this spec and validation should say so "
        f"once, got {problems}"
    )
    assert "other.py" in problems[0], (
        "the message names the offending path, because a refusal that does not "
        "say which finding failed sends the author looking through all of them"
    )
    assert "not in the diff" in problems[0], (
        "the reason distinguishes this from an out-of-hunk line, which has a "
        "different remedy — file-level rather than filing an issue"
    )


def test_validation_rejects_an_off_diff_finding_with_no_issue() -> None:
    """A finding with nowhere to live must not vanish silently."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": 1, "title": "t", "body": "b"}])

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"a finding with neither a path nor an issue has no home at all, got {problems}"
    )
    assert "issue" in problems[0], (
        "the remedy is to file an issue and record its number; naming it in the "
        "message is what stops the finding being dropped instead"
    )


def test_validation_rejects_an_inline_anchor_on_a_patch_less_file() -> None:
    """The patch-absent branch, from the plan's Not-reached-by row."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"huge.txt": None})
    spec = _spec([{"id": 1, "title": "t", "body": "b", "path": "huge.txt", "line": 4}])

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"the file is in the diff but carries no rendered lines, so the line "
        f"anchor is the only thing wrong, got {problems}"
    )
    assert "file level" in problems[0], (
        "the remedy is to drop the line and post file-level; a message that only "
        "said 'invalid' would leave the author guessing between three fixes"
    )


def test_validation_refuses_to_duplicate_an_existing_marker() -> None:
    """Re-posting a pass would double every thread."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": 1, "title": "t", "body": "b", "path": "x.py", "line": 17}])

    # Act
    problems = module.validate(spec, diff, existing_markers={"improve #1"})

    # Assert
    assert len(problems) == 1, (
        f"the marker already exists, so posting again would create a second "
        f"thread for one finding, got {problems}"
    )
    assert "already exists" in problems[0], (
        "re-posting is out of scope for this change; the refusal makes that "
        "boundary visible instead of silently doubling the gate's workload"
    )


def test_validation_reports_every_problem_not_just_the_first() -> None:
    """One refusal, naming everything wrong."""
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec(
        [
            {"id": 1, "title": "t", "body": "b", "path": "other.py", "line": 3},
            {"id": 2, "title": "t", "body": "b", "path": "x.py", "line": 21},
            {"id": 3, "title": "t", "body": "b"},
        ]
    )

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 3, (
        f"stopping at the first problem means the author fixes one, re-runs, and "
        f"finds the next — the round trips validation exists to remove, got "
        f"{problems}"
    )


# ── Marker extraction and pagination ─────────────────────────────────────────


def test_existing_markers_reads_thread_openers() -> None:
    """The duplicate guard rests entirely on this extraction.

    ``validate`` is tested with a hand-built set, which proves it *uses* the set
    correctly and proves nothing about whether the extraction produces it.
    """
    # Arrange
    module = _load_script_module()
    comments = [
        {"body": "**improve #1** — a correctness finding\n\ndetail"},
        {"body": "**ponytail #4** — a shrink\n\ndetail"},
    ]

    # Act
    found = module.existing_markers(comments)

    # Assert
    assert found == {"improve #1", "ponytail #4"}, (
        f"these are the markers the duplicate refusal compares against; if the "
        f"extraction misses one, a pass can be posted twice with every test still "
        f"green, got {found}"
    )


def test_existing_markers_ignores_disposition_replies() -> None:
    """``/pulls/{n}/comments`` returns replies too, and they are not markers."""
    # Arrange
    module = _load_script_module()
    comments = [
        {"body": "**improve #1** — a finding"},
        {"body": "**Fixed** — restored in df72b0e"},
        {"body": "**No change** — matches the surrounding convention"},
    ]

    # Act
    found = module.existing_markers(comments)

    # Assert
    assert found == {"improve #1"}, (
        f"a disposition verb is not a finding marker; collecting it makes the set "
        f"contain something other than what its name claims, and the next person "
        f"to reason about it reasons about something untrue, got {found}"
    )


def test_paged_flattens_the_pages_gh_slurp_returns() -> None:
    """``--paginate`` alone emits ``[…][…]``, which is not valid JSON.

    ``gh api --help``: "Each page is a separate JSON array or object. Pass
    ``--slurp`` to wrap all pages of JSON arrays or objects into an outer JSON
    array." Without it, ``json.loads`` raises ``Extra data`` on any pull request
    large enough to paginate — which is invisible on every pull request small
    enough not to.
    """
    # Arrange
    module = _load_script_module()
    slurped = '[[{"filename": "a.py"}], [{"filename": "b.py"}]]'

    # Act
    items = module.paged(slurped)

    # Assert
    assert items == [{"filename": "a.py"}, {"filename": "b.py"}], (
        f"the pages must arrive as one flat list; leaving them nested makes every "
        f"consumer read page objects as file entries, got {items}"
    )


def test_paged_handles_a_single_page() -> None:
    """The common case must not regress while fixing the rare one."""
    # Arrange
    module = _load_script_module()

    # Act
    items = module.paged('[[{"filename": "a.py"}]]')

    # Assert
    assert items == [{"filename": "a.py"}], (
        "nearly every pull request fits in one page, so this is the path that "
        "actually runs; breaking it to fix pagination would trade a rare failure "
        "for a universal one"
    )


# ── Rendering ────────────────────────────────────────────────────────────────


def test_the_review_body_states_where_every_finding_went() -> None:
    """The body is the one place that reconciles the pass's count.

    File-level threads cannot live inside a review, so a pass's findings are
    physically split across a review and N loose comments. Without this table
    nothing says what the pass produced.
    """
    # Arrange
    module = _load_script_module()
    spec = _spec(
        [
            {"id": 1, "title": "inline one", "body": "b", "path": "x.py", "line": 17},
            {"id": 2, "title": "file one", "body": "b", "path": "x.py"},
            {"id": 3, "title": "off-diff one", "body": "b", "issue": 2011},
        ]
    )

    # Act
    body = module.review_body(spec)

    # Assert
    assert "/improve branch — 3 findings" in body, (
        "the count is the reconciliation point; a body that does not state it "
        "cannot be checked against the threads that exist"
    )
    assert "scoped three-dot against the merge-base" in body, (
        "the narrative records how the pass was scoped, which is what makes its "
        "findings interpretable months later"
    )
    assert "inline `x.py:17`" in body, (
        "saying where each finding was posted is how a reader knows to look in "
        "the diff rather than for a loose comment"
    )
    assert "file-level `x.py`" in body, (
        "file-level threads render outside the review, so the index is the only "
        "thing tying them back to the pass that produced them"
    )
    assert "filed #2011" in body, (
        "an off-diff finding has no thread at all; without this row it is absent "
        "from the PR entirely, which is exactly WF-079's failure"
    )


def test_a_comment_body_leads_with_its_marker() -> None:
    """The disposer looks threads up by this prefix."""
    # Arrange
    module = _load_script_module()

    # Act
    body = module.comment_body(
        "improve", {"id": 2, "title": "a title", "body": "detail"}
    )

    # Assert
    assert body.startswith("**improve #2** — a title"), (
        "the disposer matches on this prefix, so a change to the format silently "
        "breaks every lookup rather than failing loudly"
    )
    assert "detail" in body, (
        "the finding's substance has to reach the thread, not just its title"
    )


# ── Whole-script behaviour ───────────────────────────────────────────────────


def test_the_script_is_executable_and_documents_itself() -> None:
    """Subprocess run of the real script — proves it parses and its args work."""
    # Arrange / Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    # Assert
    assert result.returncode == 0, (
        f"the justfile invokes this as a standalone command; --help exited "
        f"{result.returncode} with {result.stderr!r}"
    )
    assert "spec" in result.stdout.lower(), (
        "the help text names the spec argument, which is the only documentation "
        "someone running it from stage 8 will see"
    )


# ── The marker's two fields (#2088) ──────────────────────────────────────────


def _load_dispose_module() -> ModuleType:
    """Load ``scripts/dispose_finding.py``, the marker's other reader.

    `scripts/` is not a package and the two scripts do not import each other,
    so the marker's form is built independently in each. That is what the
    agreement test below pins.
    """
    path = _REPO_ROOT / "scripts" / "dispose_finding.py"
    spec = importlib.util.spec_from_file_location("dispose_finding_under_test", path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validation_rejects_a_non_integer_finding_id() -> None:
    r"""A marker this script writes must be one its readers can match.

    `dispose_finding.py` takes an integer, and `_MARKER` reads `#\\d+`, so a
    non-numeric id posts threads that no disposition can reach and that the
    duplicate guard cannot see (#2088).
    """
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": "F1", "title": "t", "body": "b", "path": "x.py"}])

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"a non-integer id must be refused before anything is written, got {problems}"
    )
    assert "F1" in problems[0], (
        "the message names the offending value, because the author has to find "
        "it in a spec file that may carry many findings"
    )


def test_validation_rejects_a_boolean_finding_id() -> None:
    """`isinstance(True, int)` is True, so a bare int check would pass this.

    `f"#{True}"` renders `#True`, which `_MARKER` cannot match — the exact
    defect the integer check exists to refuse (#2088).
    """
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": True, "title": "t", "body": "b", "path": "x.py"}])

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"a bool id renders as #True and is unmatchable, so it must be refused "
        f"like any other non-integer, got {problems}"
    )


def test_validation_rejects_a_slug_that_cannot_appear_in_a_marker() -> None:
    """The id is only half the marker; the slug is interpolated too.

    `_MARKER` reads `[^*]+` for the slug, so a slug holding an asterisk blinds
    the duplicate guard exactly as a bad id does (#2088).
    """
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": 1, "title": "t", "body": "b", "path": "x.py"}], slug="my*pass")

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"a slug holding an asterisk is unreadable by the guard that refuses a "
        f"second post, so it must be refused at validation, got {problems}"
    )
    assert "my*pass" in problems[0], (
        "the message names the slug, because it is declared once at the top of "
        "the spec and applies to every finding under it"
    )


def test_validation_rejects_a_slug_that_is_not_a_string() -> None:
    """A spec is JSON, so `slug` can arrive as any type.

    The asterisk test covers a string the guard cannot read. This covers the
    other arm: a value that is not a string at all, which `"*" in slug` would
    raise on for an integer and pass silently for a list (#2088).
    """
    # Arrange
    module = _load_script_module()
    diff = module.DiffIndex({"x.py": _PATCH})
    spec = _spec([{"id": 1, "title": "t", "body": "b", "path": "x.py"}], slug=123)

    # Act
    problems = module.validate(spec, diff, existing_markers=set())

    # Assert
    assert len(problems) == 1, (
        f"a non-string slug must be refused before it is interpolated into a "
        f"marker, got {problems}"
    )
    assert "123" in problems[0], (
        "the message names the value, because a spec file carries the slug once "
        "and every finding under it inherits the fault"
    )


def test_the_marker_form_agrees_across_both_scripts() -> None:
    """One marker form is built in three places, in two scripts.

    `post_review_findings.marker` writes it, `_MARKER` reads it, and
    `dispose_finding` builds it again to find a thread. The scripts do not
    import each other, so nothing but this test holds the three together
    (#2088).
    """
    # Arrange
    post = _load_script_module()
    dispose = _load_dispose_module()
    slug, finding_id = "improve", 7
    written = f"**{post.marker(slug, finding_id)}**"
    # A thread as `dispose_finding` sees one, carrying exactly what the writer
    # produces. Asking `find_thread` to locate it exercises that script's own
    # construction of the marker — comparing against a literal spelled here
    # would only pin this file against itself.
    thread = {"comments": {"nodes": [{"body": f"{written} — a title"}]}}

    # Act
    found = dispose.find_thread([thread], slug, finding_id)

    # Assert
    assert found is thread, (
        f"`dispose_finding` could not find a thread whose marker "
        f"`post_review_findings` wrote as {written!r}. The two scripts do not "
        f"import each other, so nothing else holds their forms together"
    )
    seen = post.existing_markers([{"body": f"{written} — a title"}])
    assert seen == {post.marker(slug, finding_id)}, (
        f"the duplicate guard must see what the writer produces, or a pass "
        f"posted twice doubles every thread instead of being refused; got {seen}"
    )
