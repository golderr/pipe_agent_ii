# LADBS Socrata Rewire Verification

Date run: 2026-04-17

Related docs:

- [ladbs_socrata_coverage_2026-04-17.md](./ladbs_socrata_coverage_2026-04-17.md)
- [ladbs_socrata_rewire_plan.md](../specs/ladbs_socrata_rewire_plan.md)

## 1. Summary

This verification reran the exact calibration cohorts from the 2026-04-17 coverage audit against
the **rewired live sources and adapters** now configured in `config/markets/los_angeles.yaml`:

- `ladbs_permits` -> `pi9x-tg5x`
- `ladbs_permit_activity` -> `pi9x-tg5x`
- `ladbs_inspections` -> `9w5z-rg2h`

Result:

1. **Pipedream permit cohort:** `15 / 15` exact permit lookups now recover through
   `ladbs_permits`. `12 / 15` also retain qualifying recent/substantive inspection evidence in
   `ladbs_inspections`.
2. **CoStar calibration cohort:** `8 / 10` sample projects return 2023+ evidence through the
   rewired sources at the adapter level, but one of those (`147c8b14`, seeded as
   `265 N Burlington Ave`) is only an APN-linked `ladbs_permit_activity` hit at
   `265 S Lucas Ave`, so the **clean audit-equivalent recovery count remains `7 / 10`**.

Comparison to the audit point estimates:

- Pipedream predicted: `~13 / 15`
- CoStar predicted: `~7 / 10`

Observed:

- Pipedream observed: `15 / 15`
- CoStar observed: `7 / 10` clean, `8 / 10` raw adapter reach

Both cohorts land inside the audit's stated tolerance band of plus or minus 30 percent of the point
estimate. The source-coverage claim is therefore verified.

## 2. Method

This pass was read-only and used the current production-intended source wiring, not the legacy
dataset IDs.

Verification path:

1. Load the active `los_angeles` market config.
2. Resolve the active adapters:
   - `ladbs_permits_pi9x_tg5x`
   - `ladbs_permit_activity_pi9x_tg5x`
   - `ladbs_inspections_9w5z_rg2h`
3. Query the live Socrata endpoints with the source config's active `effective_where` clauses plus
   the cohort-specific predicates.
4. Pass each returned row through the current adapter and evaluate the resulting `RawRecord`.

Query rules:

- Pipedream cohort:
  - `ladbs_permits`: exact `permit_nbr='<permit>'`
  - `ladbs_inspections`: exact space-normalized `permit='NNNNN NNNNN NNNNN'`
- CoStar cohort:
  - `ladbs_permits`: seeded APN query and `(zip_code, leading address number)` query
  - `ladbs_permit_activity`: same APN and address probes for the non-`Bldg-New` slice
  - For comparability with section 5a of the coverage audit, a project counted as recovered only
    when the rewired source returned **2023+** evidence.

Important interpretation rule:

- `147c8b14` returns non-`Bldg-New` activity on the seeded APN, but the adapted address resolves to
  `265 S Lucas Ave`, not the seeded `265 N Burlington Ave`. That is real source reach on the APN,
  but not a clean address-consistent recovery of the sampled seed row. It is reported separately as
  raw adapter reach, not as a clean recall win.

## 3. Pipedream Permit Cohort (15 permits from audit section 3.2)

All 15 permits now recover through the rewired `ladbs_permits` source.

| permit | `ladbs_permits` hit | adapted canonical address | issue date | `ladbs_inspections` hit | latest inspection |
|---|---|---|---|---|---|
| `18010-10000-03620` | yes | `329 SOUTH BONNIE BRAE STREET LOS ANGELES CA 90057` | `2023-05-23` | yes | `2026-03-25` |
| `23010-10000-00516` | yes | `1925 WEST MONTROSE STREET LOS ANGELES CA 90026` | `2025-05-02` | yes | `2026-04-06` |
| `21010-10000-04285` | yes | `10978 WEST WILKINS AVENUE LOS ANGELES CA 90024` | `2024-09-30` | no | - |
| `19010-20000-05733` | yes | `3555 SOUTH OVERLAND AVENUE LOS ANGELES CA 90034` | `2024-08-23` | yes | `2026-04-06` |
| `19010-10000-00654` | yes | `668 SOUTH CORONADO STREET LOS ANGELES CA 90057` | `2023-07-18` | yes | `2026-04-10` |
| `22010-10000-06040` | yes | `6066 WEST OLYMPIC BOULEVARD LOS ANGELES CA 90036` | `2025-03-04` | yes | `2026-04-10` |
| `21010-10000-00744` | yes | `1002 NORTH ALFRED STREET LOS ANGELES CA 90069` | `2024-03-13` | yes | `2026-04-01` |
| `18010-10000-01517` | yes | `684 SOUTH NEW HAMPSHIRE AVENUE LOS ANGELES CA 90005` | `2023-08-09` | yes | `2026-03-13` |
| `21010-10000-04317` | yes | `1655 NORTH ALLESANDRO STREET LOS ANGELES CA 90026` | `2024-02-28` | yes | `2026-04-07` |
| `20010-10000-04305` | yes | `549 SOUTH HARVARD BOULEVARD LOS ANGELES CA 90020` | `2023-05-30` | yes | `2026-04-03` |
| `19010-10000-05729` | yes | `710 NORTH VIRGIL AVENUE LOS ANGELES CA 90029` | `2022-05-17` | no | - |
| `22010-10000-04890` | yes | `10610 WEST PICO BOULEVARD LOS ANGELES CA 90064` | `2024-09-25` | no | - |
| `20010-10000-03127` | yes | `2121 SOUTH WESTWOOD BOULEVARD LOS ANGELES CA 90025` | `2022-08-11` | yes | `2026-02-23` |
| `19010-10000-02601` | yes | `255 SOUTH BURLINGTON AVENUE LOS ANGELES CA 90057` | `2023-11-16` | yes | `2025-08-12` |
| `23010-10000-00914` | yes | `10505 WEST WASHINGTON BOULEVARD LOS ANGELES CA 90232` | `2025-08-08` | yes | `2025-10-10` |

Tally:

- `ladbs_permits`: `15 / 15`
- `ladbs_inspections` qualifying direct UC evidence: `12 / 15`

Interpretation:

- The observed `15 / 15` is stronger than the audit's `~13 / 15` point estimate because the rewired
  `pi9x-tg5x` source covers **2020-present**, so the two pre-freeze permits that already existed in
  the legacy snapshot (`19010-10000-05729` and `20010-10000-03127`) are also present in the new
  feed.
- The `12 / 15` inspections count reflects the conservative live rule now used by the adapter:
  only recent, substantive inspections on active permits emit direct `Under Construction` evidence.
- The address mismatches called out in the audit are reproduced by the adapted canonical addresses,
  for example `10978 W Wilkins Ave` instead of the seeded `1402 S Veteran Ave`, and
  `10610 W Pico Blvd` instead of `10608 W Pico Blvd`. That confirms the rewire fixes source
  coverage but does not remove the need for identifier-first matching and a later seed-cleanup pass.

## 4. CoStar Calibration Cohort (10 cases from audit section 5a)

The table below reports **clean** recovery, with the address-ambiguous `147c8b14` APN-only
activity hit called out separately.

| short id | project | seeded address | seeded APN(s) | clean recovered? | source | note |
|---|---|---|---|---|---|---|
| `07fa917c` | The Clark on 54th | `5353 CRENSHAW BLVD 90043` | `5006-006-007` | yes | `ladbs_permits` | `Bldg-New` permit at `3409 W 54TH ST`, same sampled parcel / alternate frontage pattern as the audit. |
| `147c8b14` | - | `265 N BURLINGTON AVE 90026` | `5159-006-029` | no | raw reach only | `ladbs_permit_activity` returns APN-linked rows at `265 S LUCAS AVE`, not a clean address-consistent recovery. |
| `254c935a` | - | `11967 MAYFIELD AVE 90049` | `4265-009-175` | yes | `ladbs_permits` | Exact adapted `Bldg-New` row at `11967 W MAYFIELD AVE`. |
| `68fd77c1` | Alveare Senior Housing | `1421 S BROADWAY 90015` | none | no | - | No APN seeded and no clean address hit. |
| `7ea4d763` | - | `1734 S BARRINGTON AVE 90025` | `4262-018-026` | yes | `ladbs_permits` | Exact adapted `Bldg-New` row at seeded address. |
| `98012028` | The Standard at Los Angeles | `3900 S FIGUEROA ST 90037` | `5037-032-003`, `5037-032-048`, `5037-032-049` | yes | `ladbs_permits` | Exact adapted `Bldg-New` row at seeded address. |
| `ad437b72` | TenTen Alfred | `1010 N ALFRED ST 90069` | `5529-007-061` | no | - | No 2023+ permits or activity on the sampled APN/address. |
| `b83b1aea` | Mama Shelter DTLA | `124 E OLYMPIC BLVD 90015` | `5139-015-041` | yes | `ladbs_permit_activity` | Adaptive reuse: multiple 2023-2026 `Bldg-Alter/Repair` rows with `use_desc='Hotel'` at the exact address. |
| `d17f26f2` | Steps on St. Andrews | `1808 S ST ANDREWS PL 90019` | `5073-014-900` | yes | `ladbs_permits` | `Bldg-New` row returned on sampled parcel/address search. |
| `efafad9b` | Peak Plaza Apartments | `316 E WASHINGTON BLVD 90015` | `5127-029-042` | yes | `ladbs_permits` | Exact adapted `Bldg-New` row at seeded address. |

Tally:

- Clean audit-equivalent recoveries: `7 / 10`
- Raw adapter reach including the address-ambiguous Burlington APN case: `8 / 10`

Interpretation:

- The clean `7 / 10` result lands exactly on the audit's point estimate.
- The raw `8 / 10` result shows that the rewired `ladbs_permit_activity` source can surface one
  more parcel-linked case than the audit counted, but it is not yet safe to promote that extra case
  into a clean recovery without seed or parcel review.

## 5. Comparison To Predicted Recovery

| cohort | audit prediction | observed | result |
|---|---|---|---|
| 15 Pipedream-cited permits | `~13 / 15` | `15 / 15` | within tolerance; stronger than predicted because `pi9x-tg5x` covers the two pre-freeze 2022 permits too |
| 10 CoStar calibration cases | `~7 / 10` | `7 / 10` clean, `8 / 10` raw reach | within tolerance; clean result matches the point estimate exactly |

Tolerance check:

- `13 / 15` with plus or minus 30 percent implies an acceptable range of roughly `9` to `17`.
- `7 / 10` with plus or minus 30 percent implies an acceptable range of roughly `5` to `9`.

Observed counts are inside both ranges.

## 6. Conclusion

The rewire closed the source-coverage gap the audit identified.

What is now verified:

1. The rewired `ladbs_permits` adapter recovers the full 15-permit Pipedream cohort from audit
   section 3.2.
2. The rewired `ladbs_inspections` adapter provides conservatively filtered direct `Under
   Construction` evidence for most of that cohort (`12 / 15` in this pass).
3. The rewired `ladbs_permit_activity` source behaves as intended on adaptive-reuse cases such as
   Mama Shelter DTLA.
4. The residual misses are no longer explained by the old frozen dataset IDs. They are now the
   expected downstream issues: address drift, alternate frontage, APN ambiguity, or genuinely absent
   permit activity.

This closes the loop on the 2026-04-17 coverage audit's point estimate. The remaining work after
this point is documentation updates plus the smaller post-rewire cleanup slice, not more source
rewiring.
