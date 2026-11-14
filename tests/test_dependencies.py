"""Validate the actual Docker application environment against its dependency pins."""

from importlib.metadata import distributions
from pathlib import Path
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


def test_installed_dependencies_match_constraints_and_are_compatible():
    constraints = Path(__file__).resolve().parents[1] / "constraints.txt"
    expected = {}
    for line_number, raw_line in enumerate(constraints.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        location = f"{constraints.name}:{line_number}"
        assert not requirement.extras and requirement.marker is None and requirement.url is None, location
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", location
        assert "*" not in specifiers[0].version, location
        name = canonicalize_name(requirement.name)
        assert name not in expected, f"Duplicate dependency at {location}: {name}"
        expected[name] = str(Version(specifiers[0].version))
    assert expected, "The application dependency constraints must not be empty"

    installed = {}
    for distribution in distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        if name == "pip":
            continue
        assert name not in installed, f"Multiple installed distributions for {name}"
        installed[name] = str(Version(distribution.version))
    assert installed == expected

    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
