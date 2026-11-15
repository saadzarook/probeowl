# Author: @SaadZarook
"""dev/site's deploy guard (#104): it renders the identity check, and never pushes anything."""

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[2] / "dev" / "site" / "check_identity.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash, as dev/site does")


def check(email: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, str(CHECK), email], capture_output=True, text=True, check=False)


def test_a_noreply_address_may_sign_the_deploy() -> None:
    assert check("27498595+saadzarook@users.noreply.github.com").returncode == 0


@pytest.mark.parametrize("email", ["someone@example.com", "", "x@users.noreply.github.com.evil"])
def test_any_other_email_is_refused_with_how_to_fix_it(email: str) -> None:
    found = check(email)
    assert found.returncode == 1
    assert "not a GitHub noreply address" in found.stderr
    assert "git config --local user.email" in found.stderr
