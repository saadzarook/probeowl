# Author: @SaadZarook
"""Reload configuration: owl serve reads its config folder again, without a restart.

The folder is checked exactly as at start, and owl serve refuses it for the same errors; then
the old configuration stays loaded. It is refused while a run is running. Kill switches and
runs live in the store, not the folder, so a reload never turns a switch off. A circuit
breaker pause runs out as it would have.
"""

from collections.abc import Callable
from pathlib import Path

from probeowl.config_folder import ConfigFolder
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.reload import ConfigReload
from probeowl.runs import RunService


class ConfigReloader:
    """Reloads owl serve's config folder in place of the configuration it loaded."""

    def __init__(self, folder: Path, config_folder: ConfigFolder, runs: RunService):
        self._folder = folder  # as owl serve was given it, as at start
        self._config_folder = config_folder
        self._runs = runs
        # Sent with every API response, so an open screen sees that owl reload loaded a new one.
        self.reloads = 0

    def reload(self, change: Callable[[], None] | None = None) -> ConfigReload:
        """Load the folder again; ConfigError (nothing changed) if it would not start.

        change: a write to make first, once no run is running, so what it wrote is loaded at
        once. If it fails, nothing is loaded and the configuration stays as it was.
        """
        # Saves hold the folder's lock while they write: the folder is never read half-saved.
        reports: list[ConfigReload] = []

        def record(old: dict[str, LoadedApplication], new: dict[str, LoadedApplication]) -> None:
            report = compare(old, new)
            self._runs.audit("config_reloaded", report.model_dump(mode="json", exclude={"note"}))
            reports.append(report)

        def load() -> dict[str, LoadedApplication]:
            if change is not None:
                change()
            return load_applications(self._folder)

        with self._config_folder.lock:
            self._runs.reload(load, record)
        self.reloads += 1
        return reports[0]


def compare(old: dict[str, LoadedApplication], new: dict[str, LoadedApplication]) -> ConfigReload:
    """What changed between two loads of the folder: applications and connectors."""
    before = _connectors(old)
    after = _connectors(new)
    return ConfigReload(
        applications=len(new),
        connectors=len(after),
        probes=sum(len(app.probes) for app in new.values()),
        applications_added=sorted(new.keys() - old.keys()),
        applications_removed=sorted(old.keys() - new.keys()),
        connectors_added=sorted(after.keys() - before.keys()),
        connectors_removed=sorted(before.keys() - after.keys()),
        connectors_changed=sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
    )


def _connectors(applications: dict[str, LoadedApplication]) -> dict[str, object]:
    return {
        f"{name}/{connector}": spec
        for name, app in applications.items()
        for connector, spec in app.connectors.items()
    }
