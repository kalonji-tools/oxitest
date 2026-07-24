"""End-to-end alias-following + Q1 dedup for doctest coverage (#1602, #1609).

Rust unit tests in ``alias.rs::tests`` and ``coverage.rs::tests`` cover the
walker and dedup logic in isolation. These tests prove the wiring: real
pyproject → subject enumeration → alias resolution → dedup → reporter.

Each test scopes ``testpaths`` to the synthetic ``mypkg`` package so only its
subjects contribute to the diagnostic set; ``--warnings`` expands the summary
so message content is assertable.
"""

from oxitest import TempDir, helpers


def test_single_hop_alias_from_private_source(tmp: TempDir) -> None:
    """Public re-export from a private module inherits the source docstring.

    ``mypkg.approx`` aliases ``mypkg._bridge._impl.approx``. The private source
    lives outside the scanned public set, so Q1 dedup does not collapse the
    AliasImport. The alias walker follows the chain to the source's docstring;
    a fully covered source ⇒ no ``doctest.coverage`` diagnostic for
    ``mypkg.approx``.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""Public package."""\n'
                "from mypkg._bridge._impl import approx as approx\n"
                '__all__ = ["approx"]\n'
            ),
            "mypkg/_bridge/__init__.py": "",
            "mypkg/_bridge/_impl.py": (
                "def approx():\n"
                '    """Approx doc.\n'
                "\n"
                "    Examples:\n"
                "        >>> 1 + 1\n"
                "        2\n"
                '    """\n'
                "    pass\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    coverage_lines = [
        line
        for line in combined.splitlines()
        if "mypkg.approx" in line and "doctest.coverage" in line
    ]
    assert not coverage_lines, (
        "the alias walker must inherit the source's docstring — a covered "
        "private source means the public re-export is covered too; a "
        "diagnostic here would mean the walker failed to follow the chain: "
        f"{coverage_lines}"
    )


def test_multi_hop_alias_reaches_class_docstring(tmp: TempDir) -> None:
    """Two-hop chain: ``mypkg.__all__`` → ``_bridge._ft.Fixture`` → ``_FixtureType``.

    The docstring lives on the private ``_FixtureType`` class. The walker must
    traverse both hops (AliasImport across files, then LocalAlias inside
    ``_bridge/_ft.py``) to reach it. A covered ``_FixtureType`` ⇒ no
    ``doctest.coverage`` diagnostic for ``mypkg.Fixture``.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": (
                "from mypkg._bridge._ft import Fixture as Fixture\n"
                '__all__ = ["Fixture"]\n'
            ),
            "mypkg/_bridge/__init__.py": "",
            "mypkg/_bridge/_ft.py": (
                "class _FixtureType:\n"
                '    """Covered fixture type.\n'
                "\n"
                "    Examples:\n"
                "        >>> 1 + 1\n"
                "        2\n"
                '    """\n'
                "    pass\n"
                "\n"
                "Fixture = _FixtureType\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    coverage_lines = [
        line
        for line in combined.splitlines()
        if "mypkg.Fixture" in line and "doctest.coverage" in line
    ]
    assert not coverage_lines, (
        "the walker must chase the two-hop chain (AliasImport → LocalAlias) "
        "to the ClassDef docstring; a diagnostic here means one of the hops "
        f"was not resolved: {coverage_lines}"
    )


def test_dedup_public_to_public_reexport(tmp: TempDir) -> None:
    """Same subject at two public paths ⇒ Q1 dedup keeps only the definition.

    ``mypkg.plugin.Plugin`` (LocalDefinition) and ``mypkg.Plugin`` (AliasImport
    pointing at ``mypkg.plugin.Plugin``) refer to the same class. Q1 dedup
    drops the re-export so only ONE ``doctest.coverage`` diagnostic fires —
    against the canonical definition-site path — even though both paths are
    scanned.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": (
                'from mypkg.plugin import Plugin as Plugin\n__all__ = ["Plugin"]\n'
            ),
            "mypkg/plugin.py": "class Plugin:\n    pass\n",
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "mypkg.plugin.Plugin" in combined, (
        "the diagnostic must name the definition-site path — that's the "
        "canonical location users need to see to add the docstring, got: "
        f"{combined!r}"
    )
    # The re-export site ``mypkg.Plugin`` must not appear as its own subject.
    # Search for lines that mention ``mypkg.Plugin`` in a coverage context
    # WITHOUT the definition-site path substring (which would just be the
    # canonical diagnostic containing the same trailing token).
    lines = combined.splitlines()
    reexport_only = [
        line
        for line in lines
        if "doctest.coverage" in line
        and "mypkg.Plugin" in line
        and "mypkg.plugin.Plugin" not in line
    ]
    assert not reexport_only, (
        "Q1 dedup must drop the AliasImport when its target is also a "
        "scanned LocalDefinition — a separate re-export diagnostic here "
        f"would mean the dedup pass missed the pair: {reexport_only}"
    )


def test_alias_cycle_produces_scanner_diagnostic(tmp: TempDir) -> None:
    """A deliberate cycle ``A = B; B = A`` surfaces as an alias-cycle diagnostic.

    The walker detects the cycle and returns ``AliasError::Cycle``; the
    orchestrator maps that into a ``doctest.coverage`` scanner diagnostic
    whose message contains the phrase ``alias cycle`` (see
    ``coverage.rs::diagnostic_for_walk_error``).
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": '__all__ = ["A"]\nA = B\nB = A\n',
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "alias cycle" in combined.lower(), (
        "the walker must surface cycles as a user-visible diagnostic so the "
        "user knows why coverage can't be computed — silent skip here would "
        f"hide a real config bug: {combined!r}"
    )
