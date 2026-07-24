"""End-to-end: strict=abort promotes coverage gaps to non-zero exit."""

from __future__ import annotations

from oxitest import TempDir, helpers


def test_abort_strict_returncode_reflects_coverage_gap(tmp: TempDir) -> None:
    """strict=abort hard-fails at collection when a subject is missing."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""
        [tool.oxitest]
        testpaths = ["mypkg"]
        strict = "abort"

        [tool.oxitest.doctest]
        scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""pkg."""\n\n__all__ = ["foo"]\n\n'
                'def foo():\n    """No examples."""\n    pass\n'
            ),
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, (
        "strict=abort promotes coverage gaps to Error, exiting non-zero; "
        f"got rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_abort_strict_with_full_coverage_returncode_zero(tmp: TempDir) -> None:
    """strict=abort with a fully-covered project is the happy path — exit 0."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""
        [tool.oxitest]
        testpaths = ["mypkg"]
        strict = "abort"

        [tool.oxitest.doctest]
        scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""pkg."""\n\n__all__ = ["foo"]\n\n'
                'def foo():\n    """Foo.\n\n'
                '    Examples:\n        >>> 1 + 1\n        2\n    """\n    pass\n'
            ),
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, (
        "strict=abort with a fully-covered project must succeed; "
        f"got rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
