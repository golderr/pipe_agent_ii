# Evidence Layer Phase 1 Follow-Ups

**Date:** 2026-04-20

This note captures decisions that were clarified during Phase 1 implementation so they are
explicit before Phase 2 begins.

## Resolution Log Semantics

`resolution_log` is discrepancy-only.

Phase 2's `resolve-all` command should write a row only when the current project value and the
resolved value differ for a resolver-owned field. It is not intended to store a full
per-project x per-field snapshot of every resolution pass.

This keeps the table bounded and makes it useful for shadow-mode validation queries.

## Deferred Phase 3 Cleanup Items

These items remain intentionally deferred and must be revisited in Phase 3:

1. `AgeRestriction.NON_AGE_RESTRICTED`
   Keep the enum value for backward compatibility in Phases 1-2. Revisit final cleanup or
   permanent retention after resolver behavior is validated against real evidence.

2. `status_confidence` to `confidence` cutover
   Keep dual-write behavior during Phases 1-3. Phase 3 must define the reader cutover plan,
   update all consumers to read `confidence`, and then decide when `status_confidence` can be
   deprecated.
