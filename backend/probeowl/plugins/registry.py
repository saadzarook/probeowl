# Author: @SaadZarook
"""Find, load and mount plugins. First-party only: a fixed list, so nothing is discovered or run
that is not in this repository.
"""

import importlib
import importlib.util
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml
from fastapi import APIRouter, FastAPI
from pydantic import Field, ValidationError

from probeowl.errors import ConfigError
from probeowl.model.common import Name, ProbeOwlModel
from probeowl.plugins.api import PluginContext, PluginManifest, Record

FIRST_PARTY: tuple[str, ...] = ("compare_files",)
PACKAGE = "plugins"
MANIFEST = "plugin.yaml"


class PluginSettings(ProbeOwlModel):
    """A person's ~/.probeowl/plugins.yaml."""

    # Not "off": YAML reads that word as false, so the key would never match.
    disabled: list[Name] = Field(default_factory=list)


@dataclass(frozen=True)
class LoadedPlugin:
    """A plugin whose manifest passed, and how to get its routes, if it has any."""

    manifest: PluginManifest
    register: Callable[[PluginContext], APIRouter] | None


def default_settings_file() -> Path:
    """Where a person turns plugins off."""
    return Path.home() / ".probeowl" / "plugins.yaml"


def plugins_off(path: Path) -> set[str]:
    """The plugins this person turned off. No file: none. A file that cannot be read: ConfigError.

    A file they meant something by and we cannot read must not quietly turn everything on.
    """
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as err:
        raise ConfigError(f"{path} cannot be read: {err}; fix it or remove it") from err
    try:
        return set(PluginSettings.model_validate(data or {}).disabled)
    except ValidationError as err:
        raise ConfigError(
            f"{path} is not valid; expected a list of plugin names under disabled:, such as "
            f"disabled: [compare_files] ({err.errors()[0]['msg']})"
        ) from err


def _manifest(name: str, package: str) -> PluginManifest:
    try:
        text = files(f"{package}.{name}").joinpath(MANIFEST).read_text(encoding="utf-8")
        manifest = PluginManifest.model_validate(yaml.safe_load(text))
    except (ModuleNotFoundError, OSError, yaml.YAMLError) as err:
        raise ConfigError(f"plugin {name}: {MANIFEST} cannot be read: {err}") from err
    except ValidationError as err:
        problem = err.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise ConfigError(f"plugin {name}: {MANIFEST}: {where}: {problem['msg']}") from err
    if manifest.name != name:
        raise ConfigError(f"plugin {name}: {MANIFEST} names it {manifest.name}")
    return manifest


def _register(name: str, package: str) -> Callable[[PluginContext], APIRouter] | None:
    module = f"{package}.{name}.backend"
    if importlib.util.find_spec(module) is None:
        return None  # a backend is optional
    found = getattr(importlib.import_module(module), "register", None)
    if not callable(found):
        raise ConfigError(f"plugin {name}: {module} has no register(context) function")
    return found


def unknown_plugins_message(off: Iterable[str], names: Sequence[str] = FIRST_PARTY) -> str | None:
    """What to warn about if a name turned off is not a plugin: a typo leaves that plugin on.

    Not refused: removing a plugin must not stop owl serve for whoever turned it off.
    """
    unknown = sorted(set(off) - set(names))
    if not unknown:
        return None
    known = ", ".join(sorted(names)) or "none yet"
    return f"plugins.yaml disables {', '.join(unknown)}, not a plugin; the plugins are: {known}"


def load_plugins(
    off: Iterable[str] = (), names: Sequence[str] = FIRST_PARTY, package: str = PACKAGE
) -> list[LoadedPlugin]:
    """The plugins that are on. A plugin's manifest is checked even if it is off."""
    off = set(off)
    manifests = {name: _manifest(name, package) for name in names}
    return [
        LoadedPlugin(manifest, _register(name, package))
        for name, manifest in manifests.items()
        if name not in off
    ]


def mount_plugins(app: FastAPI, plugins: Sequence[LoadedPlugin], record: Record) -> None:
    """Add each plugin's routes under /api/plugins/<name>. One that is off has none to add."""
    for plugin in plugins:
        if plugin.register is not None:
            name = plugin.manifest.name
            app.include_router(
                plugin.register(PluginContext(plugin.manifest, record)),
                prefix=f"/api/plugins/{name}",
                tags=[f"plugin {name}"],
            )
