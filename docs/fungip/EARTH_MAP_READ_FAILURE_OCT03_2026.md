# Species map read failures — October 3, 2026

The existing Earth viewport consumes `/api/mindex/earth/map/bbox`, separately
from `/unified-search/earth`. Its species/sightings query reads canonical stored
observations and taxonomy. Previously a database error passed through the
generic `_safe_query` helper as an empty array, producing a misleading HTTP200.

Species and sightings now return a sanitized HTTP503 on query/fetch failure.
A successful query with no rows still returns HTTP200 and an empty result.
Driver exception messages and connection details are excluded from response
and warning text. Other map domains retain their existing query path.
No SQL projection, record, schema, provider request or event behavior changes.

Focused validation: the existing actual-handler/SQL-contract suite plus four
in-process HTTP cases pass **15/15**. Before the correction, **13 passed and two
failed**, demonstrating the erroneous empty200 for both species and sightings.
The database transport is synthetic; this does not establish real PostGIS or
rendered-map acceptance. The separately retained native observation can be
qualified by its sole runtime owner after exact source binding and a restricted
read-only map-handler mount. No production database or service was changed.
