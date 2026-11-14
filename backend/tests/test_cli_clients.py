# Author: @SaadZarook
"""Commands that run through owl serve say so; a --config given to one says where it goes."""

import re

import pytest
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli.server import NOT_RUN

# Typer forces a coloured, boxed help when GITHUB_ACTIONS is set, as in CI. Colour codes can
# fall inside a word (each dash of --config), so they go; box lines become spaces.
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")
_BOX = re.compile(r"[\u2500-\u257f]")


def _plain(output: str) -> str:
    """The help's words alone: no colour codes, box lines or line breaks."""
    return " ".join(_BOX.sub(" ", _COLOUR.sub("", output)).split())


CLIENTS = [
    ["validate", "topup", "tp_91f2c7", "--env", "uat", "--reason", "incident"],
    ["inspect", "C204518", "--env", "uat", "--reason", "incident"],
    ["catalog", "import", "wallet_db", "--application", "wallet", "--env", "uat"],
    ["kill", "list"],
    ["kill", "on", "--all", "--reason", "incident", "--reference", "INC-1"],
    ["kill", "off", "--all", "--reason", "incident", "--reference", "INC-1"],
]


@pytest.mark.parametrize("args", CLIENTS, ids=lambda a: " ".join(a[:2]))
def test_a_client_given_config_stops_and_names_owl_serve(args: list[str]) -> None:
    # Stops before any request: no owl serve is running here, and none is needed to say this.
    result = CliRunner().invoke(cli.app, [*args, "--config", "../examples"])
    assert result.exit_code == NOT_RUN, result.output
    assert "takes no --config: it runs through owl serve" in result.output
    assert "owl serve --config ../examples" in result.output


def test_config_alone_gets_the_same_answer_before_missing_arguments() -> None:
    result = CliRunner().invoke(cli.app, ["validate", "--config", "cfg"], prog_name="owl")
    assert result.exit_code == NOT_RUN, result.output
    assert "owl validate takes no --config" in result.output


def test_the_hidden_option_is_not_in_a_clients_help() -> None:
    result = CliRunner().invoke(cli.app, ["validate", "--help"])
    assert result.exit_code == 0
    assert "--config" not in result.output


def test_the_root_help_says_which_commands_are_clients_of_owl_serve() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])
    assert "validate, inspect, catalog, kill and reload" in _plain(result.output)
    assert "start owl serve --config <folder> first" in _plain(result.output)


@pytest.mark.parametrize(
    ("command", "says"),
    [
        (
            [],
            "New here? Run owl serve and open the browser; it walks you through setup. "
            "owl init does the same in the terminal, for scripts.",
        ),
        (["serve"], "New here? Open the address it prints in the browser"),
        (["init"], "In the terminal, for scripts"),
    ],
)
def test_the_help_sends_a_newcomer_to_the_browser_first(command: list[str], says: str) -> None:
    result = CliRunner().invoke(cli.app, [*command, "--help"])
    assert result.exit_code == 0
    assert says in _plain(result.output)
