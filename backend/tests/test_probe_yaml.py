# Author: @SaadZarook
"""The editor opens a probe's and an entity's real file, comments and line endings included."""

from pathlib import Path

import pytest

from tests.test_entity_save import Both

PROBE = "/api/applications/wallet/probes/topup_completed/yaml"
ENTITY = "/api/applications/wallet/entities/topup/yaml"


@pytest.fixture
def folder(tmp_path: Path) -> Both:
    return Both(tmp_path)


def test_a_probes_file_comes_back_exactly_as_it_is(folder: Both) -> None:
    response = folder.api.get(PROBE)
    assert response.status_code == 200
    file = response.json()
    assert file["path"] == "wallet/probes/topup_completed.yaml"
    assert file["text"] == folder.path.read_bytes().decode("utf-8")
    assert "Did a top-up complete everywhere?" in file["text"]  # a comment no model would keep


def test_an_entitys_file_comes_back_exactly_as_it_is(folder: Both) -> None:
    file = folder.api.get(ENTITY).json()
    assert file["path"] == "wallet/entities/topup.yaml"
    assert file["text"] == folder.entity_path.read_bytes().decode("utf-8")


def test_opening_and_saving_unchanged_changes_nothing(folder: Both) -> None:
    text = folder.api.get(PROBE).json()["text"]
    change = folder.save(text, env="uat").json()
    assert change["diff"] == ""


def test_one_edit_changes_one_line_and_keeps_every_comment(folder: Both) -> None:
    text = folder.api.get(PROBE).json()["text"]
    edited = text.replace("owner: payments-team", "owner: another-team")
    preview = folder.save(edited, env="uat").json()
    changed = [
        line
        for line in preview["diff"].splitlines()
        if line[:1] in "+-" and line[:3] not in {"+++", "---"}
    ]
    assert changed == ["-owner: payments-team", "+owner: another-team"]
    folder.save(edited, preview["base"], env="uat")
    assert "Did a top-up complete everywhere?" in folder.path.read_text(encoding="utf-8")


def test_a_file_with_windows_line_endings_stays_that_way(folder: Both) -> None:
    crlf = folder.text.replace("\n", "\r\n").encode("utf-8")
    folder.path.write_bytes(crlf)
    text = folder.api.get(PROBE).json()["text"]
    assert text.encode("utf-8") == crlf
    # The editor hands back \n; only the real change shows, and \r\n is kept.
    edited = text.replace("\r\n", "\n").replace("owner: payments-team", "owner: another-team")
    preview = folder.save(edited, env="uat").json()
    folder.save(edited, preview["base"], env="uat")
    assert folder.path.read_bytes().count(b"\r\n") == crlf.count(b"\r\n")


def test_it_reads_the_file_as_it_is_now(folder: Both) -> None:
    now = f"{folder.text}# edited outside\n"
    folder.path.write_text(now, encoding="utf-8", newline="")
    assert folder.api.get(PROBE).json()["text"] == now


@pytest.mark.parametrize(
    "url",
    [
        "/api/applications/nope/probes/topup_completed/yaml",
        "/api/applications/wallet/probes/nope/yaml",
        "/api/applications/nope/entities/topup/yaml",
        "/api/applications/wallet/entities/nope/yaml",
    ],
)
def test_what_does_not_exist_is_a_404(folder: Both, url: str) -> None:
    response = folder.api.get(url)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_a_name_cannot_steer_the_path(folder: Both) -> None:
    assert (
        folder.api.get("/api/applications/wallet/probes/..%2F..%2Fsecret/yaml").status_code == 404
    )


def test_a_file_deleted_since_loading_is_a_404_that_says_so(folder: Both) -> None:
    folder.path.unlink()
    response = folder.api.get(PROBE)
    assert response.status_code == 404
    assert "no longer exists" in response.json()["detail"]


def test_a_file_with_other_documents_is_refused(folder: Both) -> None:
    folder.path.write_text(f"{folder.text.rstrip()}\n---\n{folder.text}", encoding="utf-8")
    response = folder.api.get(PROBE)
    assert response.status_code == 422
    assert "holds more than probe topup_completed" in response.json()["detail"]


def test_a_saved_file_is_what_opens_next(folder: Both) -> None:
    edited = folder.text.replace("owner: payments-team", "owner: another-team")
    preview = folder.save(edited, env="uat").json()
    folder.save(edited, preview["base"], env="uat")
    assert folder.api.get(PROBE).json()["text"] == folder.path.read_bytes().decode("utf-8")
    assert "owner: another-team" in folder.path.read_text(encoding="utf-8")


def test_a_new_probes_file_opens_without_a_restart(folder: Both) -> None:
    new = folder.text.replace("probe: topup_completed", "probe: topup_new")
    url = "/api/applications/wallet/probes/topup_new"
    body = {"probe": new, "environment": "uat"}
    preview = folder.api.put(url, json=body).json()
    folder.api.put(url, json=body | {"base": preview["base"]})
    assert folder.api.get(f"{url}/yaml").json()["path"] == "wallet/probes/topup_new.yaml"


def test_no_host_login_or_secret_reference_comes_back(folder: Both) -> None:
    for url in (PROBE, ENTITY):
        text = folder.api.get(url).text
        for value in ("127.0.0.1", "probeowl_ro", "keyring:", "env:PROBEOWL"):
            assert value not in text


def test_without_a_config_folder_it_is_a_501(folder: Both) -> None:
    from probeowl.api.app import create_app
    from tests.local_client import local_client

    response = local_client(create_app()).get(PROBE)
    assert response.status_code == 501
