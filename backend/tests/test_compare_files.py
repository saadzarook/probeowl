# Author: @SaadZarook
"""The Compare files plugin: formats, masking, Reveal, limits and the audit."""

import json
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plugins.compare_files.backend import MAX_REQUEST_BYTES, _secrets
from plugins.compare_files.backend.parsers import Format, Parsed, detect, parse
from plugins.compare_files.backend.secrets import Offsets, looks_secret, looks_secret_line

from probeowl.api.app import create_app
from probeowl.errors import RequestError
from probeowl.plugins.api import LocalInput
from probeowl.plugins.registry import load_plugins
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client

URL = "/api/plugins/compare_files/compare"
SECRET = "s3cr3t-Value-9"


@pytest.fixture
def api(setup: Setup) -> Iterator[TestClient]:
    yield local_client(create_app(setup.service(), plugins=load_plugins()))


def post(api: TestClient, left: str, right: str, **body: Any) -> Any:
    response = api.post(
        URL,
        json={"left": {"name": "a", "text": left}, "right": {"name": "b", "text": right}} | body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def paths(result: dict[str, Any]) -> dict[str, tuple[str, Any, Any]]:
    return {d["path"]: (d["change"], d["left"], d["right"]) for d in result["differences"]}


# --- The plugin itself ---------------------------------------------------------------------------


def test_it_is_a_first_party_plugin_declaring_only_local_input(api: TestClient) -> None:
    [listed] = api.get("/api/plugins").json()
    assert listed["name"] == "compare_files"
    assert listed["capabilities"] == ["local_input"]


def test_turned_off_its_route_does_not_exist(setup: Setup) -> None:
    api = local_client(create_app(setup.service(), plugins=load_plugins({"compare_files"})))
    response = api.post(
        URL, json={"left": {"name": "a", "text": ""}, "right": {"name": "b", "text": ""}}
    )
    assert response.status_code == 404


# --- Formats -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('{"a": 1}', Format.JSON),
        ("[1, 2]", Format.JSON),
        ("<a><b>1</b></a>", Format.XML),
        ("a: 1\nb:\n  c: 2\n", Format.YAML),
        ("- 1\n- 2\n", Format.YAML),
        ("id,amount\n1,2\n3,4\n", Format.CSV),
        ("id;amount\n1;2\n", Format.CSV),
        ("[db]\nhost = x\nport = 1\n", Format.INI),
        ("a.b=1\nc.d=2\n", Format.PROPERTIES),
        ("# comment\nDB_HOST=x\nexport DB_PORT=1\n", Format.ENV),
        ("just some words\nand some more\n", Format.TEXT),
        ("", Format.TEXT),
    ],
    ids=lambda v: v.value if isinstance(v, Format) else None,
)
def test_formats_are_detected_from_the_content(text: str, found: Format) -> None:
    assert detect(text) is found


@pytest.mark.parametrize(
    ("format_", "left", "right", "expected"),
    [
        ("json", '{"p": {"r": 3}}', '{"p": {"r": 5}}', "p.r"),
        ("yaml", "p:\n  r: 3\n", "p:\n  r: 5\n", "p.r"),
        ("xml", "<p><r>3</r></p>", "<p><r>5</r></p>", "p.r"),
        ("xml", '<p r="3"/>', '<p r="5"/>', "p.@r"),
        ("properties", "p.r=3\n", "p.r=5\n", "p.r"),
        ("ini", "[p]\nr=3\n", "[p]\nr=5\n", "p.r"),
        ("env", "R=3\n", "R=5\n", "R"),
        ("csv", "r\n3\n", "r\n5\n", "[0].r"),
        ("text", "r 3\n", "r 5\n", "line 1"),
    ],
    ids=lambda v: v if v in {"json", "yaml", "properties", "ini", "env", "csv", "text"} else None,
)
def test_each_format_compares_by_path(
    api: TestClient, format_: str, left: str, right: str, expected: str
) -> None:
    result = post(api, left, right, format=format_)
    assert result["summary"]["changed"] == 1
    assert result["differences"][0]["path"] == expected
    assert result["left"]["format"] == format_


def test_detection_is_the_default_and_an_override_wins(api: TestClient) -> None:
    assert post(api, "a: 1\n", "a: 2\n")["left"]["format"] == "yaml"
    forced = post(api, "a: 1\n", "a: 2\n", format="text")
    assert forced["left"]["format"] == "text"
    assert forced["differences"][0]["path"] == "line 1"


def test_files_that_look_different_are_compared_as_text_and_it_says_so(api: TestClient) -> None:
    result = post(api, '{"a": 1}', "a: 1\n")
    assert result["left"]["format"] == "text"
    assert "look like different formats (json and yaml)" in result["notes"][0]


def test_csv_rows_match_by_a_chosen_key_column(api: TestClient) -> None:
    left = "id,v\na,1\nb,2\n"
    right = "id,v\nb,2\na,9\nc,3\n"
    result = post(api, left, right, format="csv", options={"arrays": "by_key", "array_key": "id"})
    found = paths(result)
    assert found["[id=a].v"][0] == "changed"
    assert "[id=c].v" in found
    assert "[id=b].v" not in found


def test_numbers_in_text_formats_honour_a_tolerance(api: TestClient) -> None:
    result = post(api, "a=1.0\n", "a=1.05\n", format="env", options={"number_tolerance": "0.1"})
    assert result["differences"] == []


def test_leading_zeros_stay_text(api: TestClient) -> None:
    assert post(api, "a=007\n", "a=7\n", format="env")["summary"]["changed"] == 1


def test_options_reach_the_engine(api: TestClient) -> None:
    result = post(
        api,
        '{"a": "X", "t": 1, "l": [1, 2]}',
        '{"a": "x", "t": 2, "l": [2, 1]}',
        options={"ignore_case": True, "ignore_paths": ["t"], "arrays": "any_order"},
    )
    assert result["differences"] == []
    assert result["summary"]["ignored"] == 1


def test_yaml_dates_and_decimals_come_back_as_strings(api: TestClient) -> None:
    found = paths(post(api, '{"a": 1.50}', '{"a": 2.50}'))
    assert found["a"] == ("changed", "1.50", "2.50")
    found = paths(post(api, "a: 2026-01-01\n", "a: 2026-01-02\n", format="yaml"))
    assert found["a"] == ("changed", "2026-01-01", "2026-01-02")


def test_two_yaml_documents_are_compared_as_a_list(api: TestClient) -> None:
    found = paths(post(api, "a: 1\n---\nb: 2\n", "a: 1\n---\nb: 3\n", format="yaml"))
    assert "[1].b" in found


# --- Files that are not what they claim ----------------------------------------------------------


@pytest.mark.parametrize(
    ("format_", "text", "message"),
    [
        ("json", '{"password": "' + SECRET, "line 1"),
        ("yaml", f"password: {SECRET}\n  bad: [", "line 2"),
        ("xml", f"<password>{SECRET}", "line 1"),
        ("csv", f"a,b\n{SECRET}\n", "row 2 has 1 of 2 columns"),
        ("csv", "a,a\n1,2\n", "a column name is repeated"),
        ("env", f"not an env line {SECRET}\n", "line 1"),
        ("ini", f"no section {SECRET}\n", "INI"),
    ],
)
def test_a_file_that_cannot_be_read_names_itself_and_never_quotes_its_content(
    api: TestClient, format_: str, text: str, message: str
) -> None:
    response = api.post(
        URL,
        json={
            "left": {"name": "bad.txt", "text": text},
            "right": {"name": "b", "text": text},
            "format": format_,
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert "bad.txt is not valid" in response.json()["detail"]
    assert message in response.json()["detail"]
    assert SECRET not in response.text


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
        '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>',
        '<!DOCTYPE a [<!ENTITY a "1"><!ENTITY b "&a;&a;&a;">]><a>&b;</a>',
    ],
    ids=["entity", "external entity", "expansion"],
)
def test_xml_entities_are_refused_not_obeyed(api: TestClient, text: str) -> None:
    response = api.post(
        URL,
        json={
            "left": {"name": "x", "text": text},
            "right": {"name": "y", "text": text},
            "format": "xml",
        },
    )
    assert response.status_code == 422
    assert "entities or a DTD" in response.json()["detail"]


def test_a_yaml_alias_bomb_is_refused_quickly() -> None:
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 12)]
    with pytest.raises(Exception, match="too many values"):
        parse("bomb.yaml", "\n".join(lines), Format.YAML)


def test_a_recursive_yaml_alias_is_refused() -> None:
    with pytest.raises(Exception, match="too many values"):
        parse("loop.yaml", "a: &a [*a]\n", Format.YAML)


@pytest.mark.parametrize("format_", ["json", "xml"])
def test_nesting_too_deep_is_refused_clearly(api: TestClient, format_: str) -> None:
    depth = 200_000
    text = "[" * depth + "]" * depth if format_ == "json" else "<a>" * depth + "</a>" * depth
    response = api.post(
        URL,
        json={
            "left": {"name": "x", "text": text},
            "right": {"name": "y", "text": text},
            "format": format_,
        },
    )
    assert response.status_code == 422


def test_yaml_tags_cannot_build_objects(api: TestClient) -> None:
    text = "a: !!python/object/apply:os.system [echo hi]\n"
    response = api.post(
        URL,
        json={
            "left": {"name": "x", "text": text},
            "right": {"name": "y", "text": text},
            "format": "yaml",
        },
    )
    assert response.status_code == 422


def test_a_file_past_the_limit_is_refused_with_a_clear_message(api: TestClient) -> None:
    big = "x" * 10_000_001
    response = api.post(
        URL, json={"left": {"name": "big.txt", "text": big}, "right": {"name": "b", "text": ""}}
    )
    assert response.status_code == 413
    assert (
        "big.txt is 10,000,001 bytes; the limit is 10,000,000 bytes per file"
        in (response.json()["detail"])
    )
    assert len(response.text) < 1000


def test_a_negative_or_impossible_tolerance_is_refused(api: TestClient) -> None:
    for tolerance in ("-1", "NaN", "Infinity", "lots"):
        response = api.post(
            URL,
            json={
                "left": {"name": "a", "text": ""},
                "right": {"name": "b", "text": ""},
                "options": {"number_tolerance": tolerance},
            },
        )
        assert response.status_code == 422, tolerance


# --- Secrets -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "DB_PASSWORD",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "secret",
        "api_key",
        "apikey",
    ],
)
def test_keys_named_like_a_secret_are_masked(key: str) -> None:
    assert looks_secret(key, "x")


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://user:hunter2@db/x",
        "Server=db;Password=hunter2",
        "Server=db;Uid=a;Pwd=hunter2;",
    ],
)
def test_connection_strings_are_masked_whatever_the_key(value: str) -> None:
    assert looks_secret("url", value)


@pytest.mark.parametrize("value", ["https://example.com/v1", "plain", "a=b", 5])
def test_ordinary_values_are_not_masked(value: object) -> None:
    assert not looks_secret("url", value)


def test_lines_of_plain_text_can_be_secret() -> None:
    assert looks_secret_line(f"password = {SECRET}")
    assert looks_secret_line(f'"api_key": "{SECRET}"')
    assert not looks_secret_line("nothing to see here")


@pytest.mark.parametrize(
    ("format_", "left", "right"),
    [
        ("json", '{"password": "%s"}', '{"password": "other-1"}'),
        ("yaml", "token: %s\n", "token: other-1\n"),
        ("env", "API_KEY=%s\n", "API_KEY=other-1\n"),
        ("properties", "db.password=%s\n", "db.password=other-1\n"),
        ("ini", "[db]\npassword=%s\n", "[db]\npassword=other-1\n"),
        ("xml", "<password>%s</password>", "<password>other-1</password>"),
        ("csv", "secret\n%s\n", "secret\nother-1\n"),
        ("text", "password = %s\n", "password = other-1\n"),
    ],
)
def test_the_server_never_returns_a_masked_value(
    api: TestClient, format_: str, left: str, right: str
) -> None:
    response = api.post(
        URL,
        json={
            "left": {"name": "a", "text": left % SECRET},
            "right": {"name": "b", "text": right},
            "format": format_,
        },
    )
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "other-1" not in response.text
    [found] = response.json()["differences"]
    assert found["masked"] is True
    assert (found["left"], found["right"]) == ("[masked]", "[masked]")


def test_reveal_says_where_the_value_is_in_each_file(api: TestClient) -> None:
    left = f'{{"name": "n", "password": "{SECRET}"}}'
    right = '{"name": "n", "password": "other-1"}'
    [found] = post(api, left, right)["differences"]
    assert left[found["reveal_left"]["start"] : found["reveal_left"]["end"]] == SECRET
    assert right[found["reveal_right"]["start"] : found["reveal_right"]["end"]] == "other-1"


def test_a_value_that_cannot_be_found_exactly_has_no_reveal(api: TestClient) -> None:
    left = json.dumps({"password": 'a"b'})  # written as a\"b in the file
    right = json.dumps({"password": "plain"})
    [found] = post(api, left, right)["differences"]
    assert found["reveal_left"] is None
    assert found["reveal_right"] is not None


def test_a_non_secret_difference_has_no_reveal(api: TestClient) -> None:
    [found] = post(api, '{"a": 1}', '{"a": 2}')["differences"]
    assert found["reveal_left"] is None and found["masked"] is False


def test_offsets_are_in_what_a_browser_counts(api: TestClient) -> None:
    left = f'{{"emoji": "🦉🦉", "password": "{SECRET}"}}'
    right = '{"emoji": "🦉🦉", "password": "other-1"}'
    [found] = post(api, left, right)["differences"]
    units = left.encode("utf-16-le")
    start, end = found["reveal_left"]["start"], found["reveal_left"]["end"]
    assert units[start * 2 : end * 2].decode("utf-16-le") == SECRET


def test_offsets_handle_astral_characters_at_the_boundary() -> None:
    units = Offsets("a🦉b")
    assert [units(0), units(1), units(2), units(3)] == [0, 1, 3, 4]


def test_side_by_side_ranges_cover_every_appearance_of_a_secret(api: TestClient) -> None:
    left = f'{{"password": "{SECRET}", "note": "{SECRET}"}}'
    result = post(api, left, left)
    assert result["differences"] == []
    hidden = result["masked_left"]
    assert [left[r["start"] : r["end"]] for r in hidden] == [SECRET, SECRET]


def test_secrets_in_ignored_paths_are_still_hidden_in_side_by_side(api: TestClient) -> None:
    left = f'{{"password": "{SECRET}"}}'
    result = post(api, left, '{"password": "x"}', options={"ignore_paths": ["password"]})
    assert result["summary"]["ignored"] == 1
    assert left[result["masked_left"][0]["start"] : result["masked_left"][0]["end"]] == SECRET


def test_secrets_in_matched_array_elements_are_still_hidden_in_side_by_side(
    api: TestClient,
) -> None:
    left = f'[{{"password": "{SECRET}"}}, 1]'
    right = f'[1, {{"password": "{SECRET}"}}]'
    result = post(api, left, right, options={"arrays": "any_order"})
    assert result["differences"] == []
    assert left[result["masked_left"][0]["start"] : result["masked_left"][0]["end"]] == SECRET


def test_an_escaped_secret_is_hidden_in_its_written_form(api: TestClient) -> None:
    left = json.dumps({"password": 'a"b'})
    result = post(api, left, left)
    [hidden] = result["masked_left"]
    assert left[hidden["start"] : hidden["end"]] == 'a\\"b'


def test_side_by_side_is_withheld_when_a_secret_cannot_be_located(api: TestClient) -> None:
    # YAML folds this into one line, which appears nowhere in the file as written.
    left = "password: >\n  one\n  two\n"
    result = post(api, left, left, format="yaml")
    assert result["masked_left"] is None and result["masked_right"] is None
    assert any("side-by-side view is hidden" in note for note in result["notes"])


def test_secret_key_field_cannot_appear_in_a_path(api: TestClient) -> None:
    left = f"token,v\n{SECRET},1\n"
    right = f"token,v\n{SECRET},2\n"
    response = api.post(
        URL,
        json={
            "left": {"name": "a", "text": left},
            "right": {"name": "b", "text": right},
            "format": "csv",
            "options": {"arrays": "by_key", "array_key": "token"},
        },
    )
    assert SECRET not in json.dumps(response.json()["differences"])
    assert "matched by position" in response.json()["notes"][0]


# --- A secret found on one side is hidden on both ------------------------------------------------

SHARED = "shared-secret-1"


def test_a_secret_under_db_password_and_an_ordinary_key_is_masked(api: TestClient) -> None:
    left = f'{{"db": {{"password": "{SHARED}"}}, "note": "a"}}'
    right = f'{{"db": {{"password": "other-secret-2"}}, "note": "{SHARED}"}}'
    response = api.post(
        URL, json={"left": {"name": "a", "text": left}, "right": {"name": "b", "text": right}}
    )
    assert SHARED not in response.text
    result = response.json()
    found = {d["path"]: d for d in result["differences"]}
    assert found["note"]["masked"] is True
    assert (found["note"]["left"], found["note"]["right"]) == ("[masked]", "[masked]")
    # Reveal still works by offset, in the file that holds it.
    reveal = found["note"]["reveal_right"]
    assert right[reveal["start"] : reveal["end"]] == SHARED
    # And the side-by-side view hides it in both files.
    assert SHARED in [right[r["start"] : r["end"]] for r in result["masked_right"]]
    assert SHARED in [left[r["start"] : r["end"]] for r in result["masked_left"]]


def test_an_added_or_removed_ordinary_key_holding_it_is_masked(api: TestClient) -> None:
    left = f'{{"token": "{SHARED}"}}'
    right = f'{{"token": "{SHARED}", "note": "{SHARED}"}}'
    response = api.post(
        URL, json={"left": {"name": "a", "text": left}, "right": {"name": "b", "text": right}}
    )
    assert SHARED not in response.text
    [found] = response.json()["differences"]
    assert (found["path"], found["change"], found["masked"]) == ("note", "added", True)


def test_a_value_merely_containing_it_is_masked_whole(api: TestClient) -> None:
    left = f'{{"password": "{SHARED}", "note": "x"}}'
    right = f'{{"password": "{SHARED}", "note": "see {SHARED} here"}}'
    response = api.post(
        URL, json={"left": {"name": "a", "text": left}, "right": {"name": "b", "text": right}}
    )
    assert SHARED not in response.text
    assert response.json()["differences"][0]["masked"] is True


def test_a_key_holding_it_does_not_show_it_in_the_path(api: TestClient) -> None:
    left = f'{{"password": "{SHARED}"}}'
    right = f'{{"password": "{SHARED}", "{SHARED}": 1}}'
    response = api.post(
        URL, json={"left": {"name": "a", "text": left}, "right": {"name": "b", "text": right}}
    )
    assert SHARED not in response.text
    assert "[masked]" in response.json()["differences"][0]["path"]


def test_it_spreads_from_plain_text_lines_too(api: TestClient) -> None:
    line = f"password = {SHARED}"
    response = api.post(
        URL,
        json={
            "left": {"name": "a", "text": f"{line}\nx\n"},
            "right": {"name": "b", "text": f"{line}\ncopy: {line}\n"},
            "format": "text",
        },
    )
    assert SHARED not in response.text


@pytest.mark.parametrize(
    ("value", "masked"),
    [("1234567", False), ("12345678", True), ("true", False), ("abc", False)],
)
def test_only_values_of_8_characters_or_more_spread(
    api: TestClient, value: str, masked: bool
) -> None:
    left = f'{{"token": "{value}", "note": "x"}}'
    right = f'{{"token": "{value}", "note": "{value}"}}'
    [found] = post(api, left, right)["differences"]
    assert found["path"] == "note"
    assert found["masked"] is masked
    assert (found["right"] == "[masked]") is masked


def test_a_short_secret_is_still_masked_where_it_was_found(api: TestClient) -> None:
    [found] = post(api, '{"token": "abc"}', '{"token": "xyz"}')["differences"]
    assert found["masked"] is True


def test_a_shared_secret_absent_from_a_file_does_not_withhold_its_view(api: TestClient) -> None:
    result = post(api, f'{{"password": "{SHARED}"}}', '{"a": 1}')
    assert result["masked_left"] is not None
    assert result["masked_right"] == []


def test_a_tree_too_deep_to_walk_for_secrets_is_refused() -> None:
    deep: Any = "x"
    for _ in range(sys.getrecursionlimit() * 2):
        deep = [deep]
    with pytest.raises(RequestError, match="nested too deeply"):
        _secrets(LocalInput("deep.json", "", 0, ""), Parsed(Format.JSON, deep))


# --- The whole request ---------------------------------------------------------------------------


def test_a_request_past_the_limit_is_refused_before_it_is_parsed(api: TestClient) -> None:
    # Not valid JSON, so reaching the parser would be a 422.
    response = api.post(
        URL,
        content=b"x" * (MAX_REQUEST_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    detail = response.json()["detail"]
    assert "too large" in detail
    assert "the limit is 25,000,000 bytes" in detail
    assert "Compare smaller files or excerpts." in detail


def test_a_chunked_request_past_the_limit_is_refused_too(api: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(26):
            yield b"x" * 1_000_000

    response = api.post(URL, content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert "the limit is 25,000,000 bytes" in response.json()["detail"]


def test_a_request_within_the_limit_still_reaches_the_route(api: TestClient) -> None:
    text = "a" * 5_000_000
    assert post(api, text, text, format="text")["differences"] == []


# --- Limits and the audit ------------------------------------------------------------------------


def test_too_many_differences_keep_the_summary_exact_and_say_so(api: TestClient) -> None:
    left = {f"k{i}": 1 for i in range(10_005)}
    right = {f"k{i}": 2 for i in range(10_005)}
    result = post(api, json.dumps(left), json.dumps(right))
    assert result["summary"]["changed"] == 10_005
    assert len(result["differences"]) == 10_000
    assert any("first 10,000 of 10,005" in note for note in result["notes"])


def test_the_audit_records_names_sizes_and_hashes_and_never_content(
    api: TestClient, setup: Setup
) -> None:
    left = f'{{"password": "{SECRET}"}}'
    post(api, left, '{"password": "other-1"}')
    with setup.store.connect() as conn:
        [event] = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "plugin_action"]
    assert event.actor.name == "s.khan"
    assert event.details["plugin"] == "compare_files"
    assert event.details["what"] == "files_compared"
    assert event.details["left"]["name"] == "a"  # type: ignore[index,call-overload]
    assert event.details["left"]["size"] == len(left)  # type: ignore[index,call-overload]
    assert len(event.details["left"]["sha256"]) == 64  # type: ignore[index,call-overload,arg-type]
    assert SECRET not in str(event.details)
    assert "other-1" not in str(event.details)


def test_a_refused_comparison_audits_nothing(api: TestClient, setup: Setup) -> None:
    api.post(
        URL,
        json={
            "left": {"name": "a", "text": "{"},
            "right": {"name": "b", "text": "{"},
            "format": "json",
        },
    )
    with setup.store.connect() as conn:
        assert not [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "plugin_action"]


def test_nothing_is_logged_or_stored(
    api: TestClient, setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    post(api, f'{{"password": "{SECRET}"}}', '{"password": "other-1"}')
    assert SECRET not in caplog.text
    with setup.store.connect() as conn:
        for table in ("runs", "evidence", "audit_events"):
            rows = conn.exec_driver_sql(f"SELECT * FROM {table}").fetchall()
            assert SECRET not in str(rows)
