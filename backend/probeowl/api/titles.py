# Author: @SaadZarook
"""The titles a run is shown with, looked up when it is read, so none is ever stored."""

from probeowl.model.check import LoadedApplication
from probeowl.model.common import humanized
from probeowl.model.entity import Entity
from probeowl.model.run import LocationResult, Run


def titled(run: Run, app: LoadedApplication | None) -> Run:
    """The run with its probe's and locations' titles as the config has them now."""
    probe = app.probes.get(run.probe) if app is not None else None
    entity = app.entities.get(run.entity) if app is not None else None
    return run.model_copy(
        update={
            "probe_title": probe.title if probe is not None else humanized(run.probe),
            "resolution": _titled(run.resolution, entity) if run.resolution else None,
            "locations": [_titled(location, entity) for location in run.locations],
        }
    )


def _titled(result: LocationResult, entity: Entity | None) -> LocationResult:
    return result.model_copy(update={"title": _location_title(result, entity)})


def _location_title(result: LocationResult, entity: Entity | None) -> str:
    # A run outlives its config: a location removed since keeps a readable name.
    found = entity.locations.get(result.parent or result.location) if entity else None
    if found is not None and result.parent is not None:
        child = found.each.get(result.location)
        return child.title if child is not None else humanized(result.location)
    return found.title if found is not None else humanized(result.location)
