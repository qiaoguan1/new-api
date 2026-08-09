# Implementation Plan

1. Define versioned fixed catalog, reviewed alias rules, source definitions, and
   publish policy as operator-editable JSON with strict validation.
2. Add dependency-free policy code for conservative parsing, mapping decisions,
   review candidates, route aggregation, and fail-closed manifest generation.
3. Add an authenticated catalog collector that reuses protected upstream
   account credentials, supports the observed NewAPI-compatible catalog shapes,
   redacts failures, and atomically preserves complete snapshots.
4. Join reviewed mappings with existing channel health and trusted actual-cost
   artifacts. Produce an internal route manifest and a privacy-safe public
   capability manifest in dry-run mode by default.
5. Add fixtures and tests for known aliases, source overrides, changing names,
   ambiguity, malformed catalogs, stale snapshots, multi-upstream routes,
   publish gates, price evidence, redaction, and idempotence.
6. Validate against current production upstream catalogs without writing prices
   or changing routes. Manually review every discovered Seedance 2.0 candidate.
7. Back up production files, deploy the collector and rules, run dry-run, enable
   the scheduled refresh, and perform ten read-only verification rounds before
   allowing any separately approved manifest activation.
