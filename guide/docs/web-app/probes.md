# Probe editor

The Probe editor is where probes are written, checked and tested.

![Probe editor: a probe's YAML, its settings and its save checks](../assets/screens/probes.png){ .screen }

- **Open probe** opens a saved probe; **New probe** starts a draft; **New entity…** builds an
  entity from the imported tables (below). On the left are its entity,
  kind, owner and the environments it's enabled in. In the middle, the **YAML** tab is the probe
  itself, **Entity YAML** is its entity, and **Key graph** shows how each location gets its key.
- **Check** runs the save checks: expectations, environments, identifiers, read-only SQL, time
  filter and PII masking, and the index check against the database. Each says what passed, why
  something failed, and what would pass. See [Writing probes](../writing-probes.md#4-save-checks).
- **Test run** runs the draft on a real ID with every guard (**Run draft**). Its version starts
  with `draft:`.
- **Save** first shows the change to the file as a diff, and confirming writes it. If the file
  changed since your preview, saving is refused and you preview again.

An existing probe opens as its real file: an edit changes only the lines you change, and keeps its
comments.

## New entity

A probe checks one entity, so the first probe of a new subject type starts with **New entity…**.
It opens the **entity graph**: the entity drawn as boxes (its locations) and arrows (the keys
between them), built from the tables imported in the environment chosen in the top bar, on any SQL
connector of the application. Import a connector's schema first, with **Import from replica** on
the [Tables](tables.md) screen. The graph opens in the application's first environment that isn't
Production.

1. **Root table**: the table one subject is a row of. The entity's id comes from its name, and its
   primary key becomes the root key; you can change either. A key named just `id` becomes
   `<entity>_id`.
2. **Sample**: one real subject's ID, usually its root key, though any identifier works. Every box
   shows its outcome on the sample, Found, Missing or Blocked with the reason, so a location that
   doesn't find the subject shows before any probe is written. **Last check** sums the boxes up.

    Outside Production the save checks run after each change, and once they pass the sample is
    looked up again, one lookup at a time. In Production nothing is read as you edit: the save checks,
    which read query plans, and the lookup run only when you press **Check sample**, with a reason
    and a reference, asked once per browser session. **Create** runs the save checks too. A draft
    failing a check is never looked up. Each lookup is a draft test run: it goes through
    every guard, kill switches and circuit breakers included, masks what it reads, and is stored and
    audited. The **Runs** screen hides draft runs unless **Show draft runs** is ticked.

3. **Locations**: the other places the subject's data lives. **Follow a key** offers the tables,
   on any connector, that share a key with a location you've added:
    - a column the catalog links to another table (`links_to`), in either direction;
    - a column with the same distinctive name, such as `provider_payment_id`;
    - a column named for a table's key, such as `cards.visitor_profile_id` for `visitor_profile.id`.

    Common names, such as `id`, `status`, `type`, `created_at` or `updated_at`, are offered only
    when the catalog links them. To link on one yourself, use **Link by columns…**: pick a column of
    a location and a column of any imported table. A location whose key column is unique holds
    one record; otherwise it's `many`, with `max_rows: 100`, or the connector's row ceiling if
    that's lower. You choose the columns each location selects.

    Outside Production every location runs the save checks as soon as it's added, and a failure shows on that
    location, with its fix where one is known. The usual one: a Transaction, Log or Audit
    table may only be read by a key or within a time window, so `cards` read by
    `visitor_profile_id` fails until the catalog calls that column a link. **Mark
    cards.visitor_profile_id as a link to visitor_profile.id** previews that change to
    `catalog.yaml`, and **Confirm in catalog.yaml** writes it, as the [Tables](tables.md) screen
    would. A failing location is never left out: fix it or remove it yourself.
4. **Identifiers**: what else can find a subject. Unique columns of a location keyed by the root
   key, such as `cards.card_id`, are ticked for you, with the reason. A unique column the catalog
   marks as PII, such as an email, is offered but not ticked; if you tick it, it's marked PII, so
   it stays masked. A pattern is optional. Only locations keyed by the root key are offered, since
   a value found there leads to its subject.
5. **Create**: runs every save check on the entity, the index check included, and shows the new
   file as a diff. **Write** writes it.

Below 1024 px, and for screen readers at any width, the graph is an outline: the same boxes as
nested lists in key order. In the graph, the arrow keys follow the keys between boxes.

New entity only creates entities. To change one, open one of its probes and edit its **Entity
YAML**, so the probes are checked with it. REST locations are written by hand: see
[Writing probes](../writing-probes.md#2-locations-where-its-data-lives).
