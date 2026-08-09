"""A test that only imports when the rootdir is the project root, not `sub/`."""

from mypkg import VALUE


def test_imports_the_project_package() -> None:
    assert VALUE == 42, (
        "the project package imports only when the rootdir reached sys.path; "
        "a rootdir of `sub/` or of the empty path makes this a "
        "ModuleNotFoundError (#2026)"
    )
