# Author: @SaadZarook
"""Check a run's audit record has its reason and reference: part of the M1 exit check."""

import sys

from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import LOCAL_WORKSPACE

store_url, run_id = sys.argv[1], sys.argv[2]
engine = create_store_engine(store_url)
with engine.connect() as conn:
    events = {event.action: event for event in list_events(conn, LOCAL_WORKSPACE, run_id)}
engine.dispose()
started = events.get("run_started")
if started is None or "run_finished" not in events:
    sys.exit(f"run {run_id}: expected run_started and run_finished, found {sorted(events)}")
found = (started.details.get("reason"), started.details.get("reference"))
if found != ("incident", "WALLET-482"):
    sys.exit(f"run {run_id}: audit reason and reference are {found}")
print(f"run {run_id}: audit has reason {found[0]}, reference {found[1]}, by {started.actor.name}")
