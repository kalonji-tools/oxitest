r"""Tests for the artifact checker in ``check_artifact.py``.

ADR-0019 gives the ``tooling`` attribute one obligation: a test with that
attribute makes its tool fail. A checker that never refuses is an instrument
that cannot report its own silence, and this file is that obligation for
``scripts/check_artifact.py``.

Every case builds a synthetic installed distribution and a synthetic source
tree, then runs the checker as a subprocess with ``PYTHONPATH`` pointed at the
synthetic ``site-packages``. The checker reads the installed distribution
through :mod:`importlib.metadata`, which resolves through ``sys.path``, so the
synthetic distribution is the one it finds. Running it in-process would make
the verdict depend on the runner's own installation of oxitest.

The Windows case is not decoration. ``RECORD`` holds the console script as
``../../../bin/oxitest`` on POSIX and ``..\\..\\Scripts\\oxitest.exe`` on
Windows, and twelve of the twenty release gate legs are macOS or Windows. A
manifest comparison over whole ``RECORD`` lines refuses every one of them for a
defect that does not exist (#2177).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from oxitest import TempDir

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_artifact.py"

_VERSION = "9.9.9"
_DIST_INFO = f"oxitest-{_VERSION}.dist-info"
_EXTENSION = "oxitest/_oxitest.cpython-312-x86_64-linux-gnu.so"


# ── Synthetic fixtures ───────────────────────────────────────────────────────


def _write_fake_install(root: Path, record_entries: list[str]) -> Path:
    """Write a site-packages holding a synthetic oxitest distribution.

    ``record_entries`` are the ``RECORD`` paths verbatim, so a case can write
    the Windows console-script spelling that no POSIX runner produces.

    Every entry under ``oxitest/`` is also written to the disk.
    :attr:`importlib.metadata.Distribution.files` passes its RECORD through
    ``skip_missing_files``, so an entry naming a file that is not there is
    dropped without a word. That is why the manifest property asserts the file
    is **present on the disk** rather than that RECORD mentions it, which is
    the stronger of the two claims and the one the band wants.
    """
    site = root / "site"
    package = site / "oxitest"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{_VERSION}"\n', encoding="utf-8"
    )
    for entry in record_entries:
        if not entry.startswith("oxitest/"):
            continue
        target = site / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("", encoding="utf-8")
    info = site / _DIST_INFO
    info.mkdir()
    (info / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp{sys.version_info[0]}{sys.version_info[1]}"
        f"-cp{sys.version_info[0]}{sys.version_info[1]}-manylinux_2_17_x86_64\n",
        encoding="utf-8",
    )
    (info / "METADATA").write_text(
        "Metadata-Version: 2.4\n"
        "Name: oxitest\n"
        f"Version: {_VERSION}\n"
        "Requires-Python: >=3.11\n"
        "Classifier: Operating System :: POSIX :: Linux\n",
        encoding="utf-8",
    )
    lines = [f"{entry},," for entry in record_entries]
    lines.append(f"{_DIST_INFO}/RECORD,,")
    (info / "RECORD").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return site


def _write_fake_source(root: Path, modules: list[str]) -> Path:
    """Write a source tree in the layout ``python-source = "python"`` declares.

    Returns the source root, which is the directory holding ``python/``, so the
    checker is given the same shape ``$GITHUB_WORKSPACE`` has.
    """
    package = root / "src" / "python" / "oxitest"
    package.mkdir(parents=True)
    for name in modules:
        (package / name).write_text("", encoding="utf-8")
    return root / "src"


def _run(
    site: Path, source_root: Path, cwd: Path, properties: str
) -> subprocess.CompletedProcess[str]:
    """Run the checker against a synthetic installation."""
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--source-root",
            str(source_root),
            "--properties",
            properties,
        ],
        cwd=str(cwd),
        env={**os.environ, "PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


# ── The manifest property ────────────────────────────────────────────────────


def test_a_module_absent_from_the_record_refuses(tmp: TempDir) -> None:
    """A wheel that omits a source module must not reach PyPI."""
    # Arrange: the source tree holds two modules, RECORD lists one.
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    source_root = _write_fake_source(tmp.path, ["__init__.py", "_absent.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manifest")

    # Assert
    assert result.returncode == 1, (
        "check_artifact.py must refuse a wheel that omits a source module —"
        " PyPI does not permit a filename to be uploaded a second time, so a"
        f" gate that cannot refuse makes the broken wheel permanent."
        f" stdout: {result.stdout} stderr: {result.stderr}"
    )
    assert "manifest" in result.stdout, (
        "the report must name the property, because a leg asserts up to five"
        " and the reader has to know which one failed"
    )
    assert "oxitest/_absent.py" in result.stdout, (
        "the report must name the missing file — without it the author has to"
        " diff two file lists by hand to find what the wheel dropped"
    )


def test_a_complete_record_passes(tmp: TempDir) -> None:
    """A wheel holding every source module must not fail the gate."""
    # Arrange
    site = _write_fake_install(
        tmp.path, ["oxitest/__init__.py", "oxitest/_present.py", _EXTENSION]
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py", "_present.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manifest")

    # Assert
    assert result.returncode == 0, (
        "a complete manifest must pass — a gate that refuses every release"
        f" blocks the upload it exists to protect. stdout: {result.stdout}"
        f" stderr: {result.stderr}"
    )


def test_a_windows_console_script_entry_does_not_fail_the_manifest(
    tmp: TempDir,
) -> None:
    """RECORD entries outside ``oxitest/`` are not package files."""
    # Arrange: the Windows console-script spelling, which no POSIX runner writes.
    site = _write_fake_install(
        tmp.path,
        ["..\\..\\Scripts\\oxitest.exe", "oxitest/__init__.py", _EXTENSION],
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manifest")

    # Assert
    assert result.returncode == 0, (
        "a console-script entry sits outside `oxitest/` and is not a package"
        " file — comparing whole RECORD lines refuses all four Windows legs"
        f" and all eight macOS legs for no defect. stdout: {result.stdout}"
    )


def test_two_compiled_extensions_refuse(tmp: TempDir) -> None:
    """Exactly one extension module ships, whatever the build produced."""
    # Arrange
    site = _write_fake_install(
        tmp.path,
        [
            "oxitest/__init__.py",
            _EXTENSION,
            "oxitest/_oxitest.cpython-313-x86_64-linux-gnu.so",
        ],
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manifest")

    # Assert
    assert result.returncode == 1, (
        "a wheel carrying two extensions was built against two interpreters —"
        " one of them cannot load, and the import property only proves the"
        f" one this leg runs. stdout: {result.stdout}"
    )


def test_a_source_root_holding_no_package_refuses(tmp: TempDir) -> None:
    """An expected set that is empty makes the comparison hold for free."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])

    # Act: --source-root names a directory with no python/oxitest under it.
    result = _run(site, tmp.path / "absent", tmp.path, "manifest")

    # Assert
    assert result.returncode == 1, (
        "with no expected file, `expected - actual` is empty and the property"
        " reports success having compared nothing — a typo in --source-root"
        f" would make the release gate inert. stdout: {result.stdout}"
    )
    assert "--source-root" in result.stdout, (
        "the report must name the argument to check, because the failure is in"
        " the invocation and not in the artifact"
    )


# ── The metadata, tag and manylinux properties ───────────────────────────────


def test_metadata_disagreeing_with_pyproject_refuses(tmp: TempDir) -> None:
    """The artifact carries what `pyproject.toml` declares, or it refuses."""
    # Arrange: the installed METADATA promises 3.10; pyproject declares 3.11.
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    info = site / _DIST_INFO
    (info / "METADATA").write_text(
        "Metadata-Version: 2.4\n"
        "Name: oxitest\n"
        f"Version: {_VERSION}\n"
        "Requires-Python: >=3.10\n"
        "Classifier: Operating System :: POSIX :: Linux\n",
        encoding="utf-8",
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])
    (source_root / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n'
        'classifiers = ["Operating System :: POSIX :: Linux"]\n',
        encoding="utf-8",
    )

    # Act
    result = _run(site, source_root, tmp.path, "metadata")

    # Assert
    assert result.returncode == 1, (
        "a wheel promising a Python the project does not support installs on"
        f" interpreters that cannot run it. stdout: {result.stdout}"
    )
    assert "Requires-Python" in result.stdout, (
        "the report must name the field that disagrees, because the property"
        " compares two of them"
    )


def test_a_classifier_only_in_the_artifact_refuses(tmp: TempDir) -> None:
    """Classifiers are derived from the tested set, never added to the wheel."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    info = site / _DIST_INFO
    (info / "METADATA").write_text(
        "Metadata-Version: 2.4\n"
        "Name: oxitest\n"
        f"Version: {_VERSION}\n"
        "Requires-Python: >=3.11\n"
        "Classifier: Operating System :: POSIX :: Linux\n"
        "Classifier: Operating System :: Microsoft :: Windows\n",
        encoding="utf-8",
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])
    (source_root / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n'
        'classifiers = ["Operating System :: POSIX :: Linux"]\n',
        encoding="utf-8",
    )

    # Act
    result = _run(site, source_root, tmp.path, "metadata")

    # Assert
    assert result.returncode == 1, (
        "a classifier the project does not declare promises a platform ADR-0013"
        f" says nothing tests. stdout: {result.stdout}"
    )
    assert "Microsoft" in result.stdout, (
        "the report must name the classifier that differs, or the author has to"
        " compare two lists by hand"
    )


def test_a_wheel_tagged_for_another_interpreter_refuses(tmp: TempDir) -> None:
    """A leg installs the wheel of its own interpreter, or the leg is wrong."""
    # Arrange: a cp999 tag, which no running interpreter can match.
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    (site / _DIST_INFO / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Root-Is-Purelib: false\n"
        "Tag: cp999-cp999-manylinux_2_17_x86_64\n",
        encoding="utf-8",
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "tag")

    # Assert
    assert result.returncode == 1, (
        "the matrix leg names an interpreter and pip picks the wheel; if the"
        " two disagree the leg examined an artifact belonging to another leg."
        f" stdout: {result.stdout}"
    )
    assert "cp999" in result.stdout, (
        "the report must name the observed tag, because the expected one is"
        " derived from the running interpreter and is not written anywhere"
    )


def test_an_unrepaired_linux_wheel_refuses(tmp: TempDir) -> None:
    """A bare `linux_` tag means auditwheel did not run, and PyPI refuses it."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    (site / _DIST_INFO / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp{sys.version_info[0]}{sys.version_info[1]}"
        f"-cp{sys.version_info[0]}{sys.version_info[1]}-linux_x86_64\n",
        encoding="utf-8",
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manylinux")

    # Assert
    assert result.returncode == 1, (
        "PyPI refuses an unrepaired Linux wheel, and the gate must refuse it"
        f" first — after the tag is cut the correction is a new version."
        f" stdout: {result.stdout}"
    )
    assert "linux_x86_64" in result.stdout, (
        "the report must name the observed platform tag, because that is what"
        " tells the author whether auditwheel ran"
    )


def test_a_newer_manylinux_than_the_release_container_refuses(
    tmp: TempDir,
) -> None:
    """A higher manylinux number excludes the distributions the tag promises."""
    # Arrange: what a local build produces outside the release container.
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    (site / _DIST_INFO / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp{sys.version_info[0]}{sys.version_info[1]}"
        f"-cp{sys.version_info[0]}{sys.version_info[1]}-manylinux_2_39_x86_64\n",
        encoding="utf-8",
    )
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manylinux")

    # Assert
    assert result.returncode == 1, (
        "`manylinux: auto` gives a manylinux2014 container and glibc 2.17; a"
        " higher number means the wheel was built somewhere else and it will"
        f" not load on the distributions v4.0.0 already serves."
        f" stdout: {result.stdout}"
    )


# ── The import property ──────────────────────────────────────────────────────


def test_the_working_directory_inside_the_source_tree_refuses(
    tmp: TempDir,
) -> None:
    """The import property is vacuous when the source tree is the cwd."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act: run from inside the source tree.
    result = _run(site, source_root, source_root, "import")

    # Assert
    assert result.returncode == 1, (
        "an import run from the source tree proves nothing about the artifact"
        " — a no-op gate is worse than no gate, because it reports coverage it"
        f" does not have. stdout: {result.stdout}"
    )


def test_an_import_from_outside_the_source_tree_passes(tmp: TempDir) -> None:
    """The artifact imports when the source tree is not the cwd."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "import")

    # Assert
    assert result.returncode == 0, (
        "the synthetic distribution is outside the synthetic source tree, so"
        f" the property holds. stdout: {result.stdout} stderr: {result.stderr}"
    )


# ── The argument surface ─────────────────────────────────────────────────────


def test_an_empty_property_list_refuses(tmp: TempDir) -> None:
    """A leg that names no property must fail, not check nothing."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "")

    # Assert
    assert result.returncode == 1, (
        "an empty --properties is a matrix leg whose `properties` key never"
        " reached it — that leg would report success while asserting nothing"
    )


def test_an_unknown_property_refuses(tmp: TempDir) -> None:
    """A misspelled property must fail rather than be skipped."""
    # Arrange
    site = _write_fake_install(tmp.path, ["oxitest/__init__.py", _EXTENSION])
    source_root = _write_fake_source(tmp.path, ["__init__.py"])

    # Act
    result = _run(site, source_root, tmp.path, "manifest,manylinx")

    # Assert
    assert result.returncode == 1, (
        "a misspelled property name silently drops an assertion — the leg"
        " stays green and the property it was added for is never checked"
    )
    assert "manylinx" in result.stdout, (
        "the report must name the unknown value, so the author sees the typo"
        " rather than hunting for which of five properties went missing"
    )
