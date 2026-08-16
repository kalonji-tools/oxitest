#!/usr/bin/env python3
r"""Assert the properties of an installed oxitest artifact.

ADR-0019 gives the Distribution band the properties an editable install cannot
reach, and this checker asserts them. It reads the *installed* distribution --
RECORD, WHEEL and METADATA -- rather than a wheel file, so it asserts what is
on the disk and needs no rule for choosing which wheel a runner owns.

The release gate in `publish.yml` names five properties. The pull-request job
in `test.yml` names two. The pull-request check is therefore a subset by
argument, and it cannot drift into an assertion the release gate does not make.

Every path comparison is on POSIX-normalised strings, and only entries under
`oxitest/` are compared. RECORD holds the console script as
`../../../bin/oxitest` on POSIX and as `..\\..\\Scripts\\oxitest.exe` on
Windows, and twelve of the twenty release gate legs are macOS or Windows. A
comparison over whole RECORD lines refuses every one of them for a defect that
does not exist.

Output is pure ASCII, for the reason `check_platform_sets.py` states: a child
process with stdout piped encodes what it prints through the locale codec.

Exits 0 when every named property holds, 1 with a report naming the property
and the value that was observed.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path

DIST_NAME = "oxitest"
PACKAGE_PREFIX = "oxitest/"

# The names `--properties` accepts. An unknown name is an error rather than a
# silent skip: a leg that names a property this checker does not know would
# report success while asserting one fewer thing than its author wrote.
PROPERTIES = ("manifest", "import", "metadata", "tag", "manylinux")

# The extension is a build output. `maturin develop` writes it into the source
# tree, so it is present there and must not be part of the expected set.
EXTENSION_PREFIX = "_oxitest."


def expected_manifest(source_root: Path) -> set[str]:
    """The package files the source tree says the artifact must hold.

    Derived from the tree, never from a written list, so a module added today
    is in the expected set today. A written list is a second copy of the
    package layout, and a module added tomorrow would not be in it.
    """
    package = source_root / "python" / "oxitest"
    expected: set[str] = set()
    for path in package.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        is_extension = path.name.startswith(EXTENSION_PREFIX) and path.suffix != ".pyi"
        if is_extension:
            continue
        if path.suffix not in {".py", ".pyi"} and path.name != "py.typed":
            continue
        expected.add(path.relative_to(package.parent).as_posix())
    return expected


def installed_manifest(dist: Distribution) -> set[str]:
    """The package files the installed distribution holds.

    `.pyc` is excluded because pip writes it at install time; it is not part of
    the artifact. Everything outside `oxitest/` is excluded -- see the module
    docstring.
    """
    files = dist.files or ()
    return {
        name
        for name in (path.as_posix() for path in files)
        if name.startswith(PACKAGE_PREFIX) and not name.endswith(".pyc")
    }


def wheel_tag(dist: Distribution) -> str:
    """The `Tag:` field of the installed WHEEL file, or an empty string."""
    for line in (dist.read_text("WHEEL") or "").splitlines():
        if line.startswith("Tag:"):
            return line.split(":", 1)[1].strip()
    return ""


def check_manifest(dist: Distribution, source_root: Path) -> list[str]:
    """Property 1 -- the file manifest of the artifact."""
    actual = installed_manifest(dist)
    failures: list[str] = []
    expected = expected_manifest(source_root)

    # Vacuity guard. `expected - actual` over an empty expected set is empty,
    # so a `--source-root` that names no package made this property report
    # success having compared nothing. Measured: a run against a directory that
    # does not exist printed `OK` and exited 0.
    if not expected:
        failures.append(
            f"property manifest: no package file was found under"
            f" {source_root / 'python' / 'oxitest'}. The comparison below would"
            f" hold having read nothing. Check --source-root."
        )
        return failures

    missing = sorted(expected - actual)
    if missing:
        listed = "\n    ".join(missing)
        failures.append(
            f"property manifest: {len(missing)} file(s) are in the source tree"
            f" and not in the artifact:\n    {listed}"
        )

    extensions = sorted(
        name
        for name in actual
        if name.startswith(PACKAGE_PREFIX + EXTENSION_PREFIX)
        and not name.endswith(".pyi")
    )
    if len(extensions) != 1:
        failures.append(
            f"property manifest: the artifact holds {len(extensions)} compiled"
            f" extension(s) and it must hold exactly 1: {extensions}"
        )
    return failures


def check_import(source_root: Path) -> list[str]:
    """Property 2 -- import when the source tree is absent."""
    cwd = Path.cwd().resolve()
    root = source_root.resolve()
    if cwd == root or root in cwd.parents:
        return [
            f"property import: the working directory {cwd} is inside the"
            f" source tree {root}, so this property proves nothing. Run the"
            f" checker from another directory."
        ]

    import oxitest  # noqa: PLC0415 -- the import is the assertion

    module = Path(oxitest.__file__ or "").resolve()
    if root in module.parents:
        return [
            f"property import: oxitest resolved to {module}, which is inside"
            f" the source tree {root}. The artifact was not imported."
        ]
    return []


def check_metadata(dist: Distribution, source_root: Path) -> list[str]:
    """Property 3 -- the `.dist-info` metadata."""
    with (source_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    failures: list[str] = []

    declared = project["requires-python"]
    installed = dist.metadata["Requires-Python"]
    if installed != declared:
        failures.append(
            f"property metadata: Requires-Python is {installed!r} and"
            f" pyproject.toml declares {declared!r}"
        )

    declared_classifiers = set(project["classifiers"])
    installed_classifiers = set(dist.metadata.get_all("Classifier") or ())
    if installed_classifiers != declared_classifiers:
        failures.append(
            "property metadata: the classifier sets differ. Only in the"
            f" artifact: {sorted(installed_classifiers - declared_classifiers)}."
            " Only in pyproject.toml:"
            f" {sorted(declared_classifiers - installed_classifiers)}"
        )
    return failures


def check_tag(dist: Distribution) -> list[str]:
    """Property 3, second half -- the wheel tag names this interpreter."""
    tag = wheel_tag(dist)
    expected = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    if not tag:
        return ["property tag: the installed WHEEL file holds no Tag: field"]
    if not tag.startswith(expected):
        return [
            f"property tag: the artifact is tagged {tag!r} and this"
            f" interpreter is {expected}"
        ]
    return []


def check_manylinux(dist: Distribution, expect: str) -> list[str]:
    """Property 4 -- the manylinux tag and the auditwheel repair."""
    tag = wheel_tag(dist)
    platform_tag = tag.split("-", 2)[-1] if tag else ""
    if not platform_tag.startswith(expect):
        return [
            f"property manylinux: the platform tag is {platform_tag!r} and it"
            f" must start with {expect!r}. A bare 'linux_' tag means auditwheel"
            " did not run, and PyPI refuses that wheel. A higher manylinux"
            " number means the wheel was built against a newer glibc than the"
            " release container gives, and it excludes the distributions the"
            " declared tag promises."
        ]
    return []


def parse_properties(raw: str) -> tuple[list[str], list[str]]:
    """Split `--properties` into the names to run and the errors to report."""
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        return [], [
            "--properties named no property. A leg that checks nothing reports"
            " success while asserting nothing."
        ]
    unknown = sorted(set(names) - set(PROPERTIES))
    if unknown:
        return [], [
            f"--properties holds unknown name(s) {unknown}. The known names are"
            f" {list(PROPERTIES)}."
        ]
    return names, []


def run(names: list[str], dist: Distribution, args: argparse.Namespace) -> list[str]:
    """Run each named property and collect every failure."""
    failures: list[str] = []
    for name in names:
        if name == "manifest":
            failures += check_manifest(dist, args.source_root)
        elif name == "import":
            failures += check_import(args.source_root)
        elif name == "metadata":
            failures += check_metadata(dist, args.source_root)
        elif name == "tag":
            failures += check_tag(dist)
        elif name == "manylinux":
            failures += check_manylinux(dist, args.expect_platform_tag)
    return failures


def main(argv: list[str] | None = None) -> int:
    """Assert the named properties, and report every failure at once."""
    parser = argparse.ArgumentParser(description="Check an installed artifact.")
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="the repository root, which holds python/oxitest and pyproject.toml",
    )
    parser.add_argument(
        "--properties",
        required=True,
        help=f"comma-separated, from {list(PROPERTIES)}",
    )
    parser.add_argument(
        "--expect-platform-tag",
        default="manylinux_2_17",
        help="the platform tag prefix the manylinux property requires",
    )
    args = parser.parse_args(argv)

    names, errors = parse_properties(args.properties)
    if errors:
        for error in errors:
            print(error)
        return 1

    try:
        dist = distribution(DIST_NAME)
    except PackageNotFoundError:
        print(f"FAIL {DIST_NAME} is not installed for {sys.executable}")
        return 1

    failures = run(names, dist, args)
    header = f"{DIST_NAME} {dist.version} ({wheel_tag(dist) or 'no tag'})"
    if failures:
        print(f"FAIL {header}")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"OK {header} -- {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
