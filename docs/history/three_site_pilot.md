# Reusable seeded three-site pilot

The pilot now optionally samples coordinates and uses zero initial calendar delay.
See `coordinate_generation.md` for the current layout/provenance checks; historical
fixed-layout observations below predate those requested parameter changes.

Maintained on `thesis-checkpoint-6`, extending checkpoint-6 commit
`e6efe50fff74d058181eb9ef0b051029aea3932f`. The user clarified that no reference
attachment was required and authorized an illustrative generated scenario.
The remote viewer enhancement `51eeeee75a516e60079d48819ba1b6a3f85de5ae` is retained.
No separate checkpoint branch or new policy is introduced.

## Configuration and generated inputs

`configs/three_site_pilot.json` has format version 3 and defaults to
`dynamic_quality_aware`. Its seed comes exclusively from `--seed`.

| Configured component | Pilot values |
|---|---|
| Horizon | 40 time units |
| CP1, CP2, CP3 fixed-layout fallback coordinates | (2,1), (6,3), (2,7) |
| RF1, RF2, RF3 fixed-layout fallback coordinates | (0,0), (8,0), (4,8) |
| Fleet | AV1 at RF1, AV2 at RF2, AV3 at RF3; speed 1, payload 2, unlimited range |
| Plant stock | 3 shared inventory slots per plant, including intermediate-operation holds |
| Stations at every plant | WS1 operation 1 duration 1; WS2 operation 2 duration 1.5; WS3 operation 3 duration 2 |
| Quality routes | Q132: [1,3,2]; Q3: [3]; Q21: [2,1]; all remanufacturable |
| Arrivals | Independent Poisson streams on [0,12]; rates CP1 .16, CP2 .12, CP3 .10 |
| Batch sizes | CP1 sizes 1/2 with weights 4/1; CP2 fixed 1; CP3 sizes 1/2 with weights 1/1 |
| Site quality weights | Explicit per-class weights under each collection point |
| Calendars | Generate independently per workstation on [0,40], initial delay [0,0], open duration uniform [8,12], closed duration uniform [1,3] |

Vehicles, capacities, operation lists, weights and processing durations are fixed
configuration values. Coordinates are fixed when coordinate_generation is absent;
the pilot now enables independent uniform XY sampling on [0,8] for both axes. A seed changes sampled
arrival times, batch sizes, qualities and calendars. Zero returned products at a
particular site is a valid Poisson outcome; it does not remove that site or AV.

The existing CLI needs no new flags. `prepare_scenario()` dispatches unresolved
format-three inputs to the new network generator; format-two single-site inputs
keep their exact original generator and generation-version-1 seed protocol.
The network output stays resolved scenario format 3 and uses generation version 2.
The experiment runner's existing format-two input scope is unchanged.

Each site's `generation` uses the original arrival/batch generator and each
site's `quality_weights` uses the original weighted quality sampler. The generator
retains recycling filtering if a configuration has no recyclers. Every eligible
remanufacturing route must be supported at every plant, preserving the existing
quality-unaware destination boundary. This pilot has no recyclers.

Calendar generation samples one initial delay, then alternating positive open
and closed durations, clipping the final opening at the configured end time.
Positive durations and floating-point progress checks prevent non-advancing loops.
Station-level `window_generation` merges with the global `schedule_generation`.
Alternatively a station can specify fixed `windows` and omit `window_generation`;
that station has no sampled schedule stream. Fixed and generated windows cannot
both be declared in an unresolved station.

The saved scenario includes actual `products`, `arrival_batches` (with site
origin), per-station `windows`, fully merged generated-window parameters and
seed provenance. Product/batch IDs are site-namespaced, e.g. `CP1:P000001` and
`CP1:B000001`; global lists are sorted by release time, origin and ID.
Canonical scenario output uses UTF-8/LF bytes. `--resolved-scenario` reads these
saved bytes directly and never calls any generator.

## Separate streams and independent auditing

`network_stream_name()` encodes identities as compact JSON arrays to avoid
separator collisions. With the existing SHA-256 `derive_stream_seed()`:

1. Derive a point seed from `['network-v2','collection',point_id]` and the CLI seed.
2. Derive independent `arrivals_batches` and `qualities` stream seeds from that
   point seed, reusing the original generation helpers with separate Random objects.
3. Derive each workstation seed directly from the CLI seed and
   `['network-v2','schedule',plant_id,station_id]`.

The provenance stores the CLI master seed, algorithm/version, point seeds and
`stream_seeds.returns`, `.qualities`, `.workstation_schedules`. IDs, not list
positions, identify streams. Reordering sites/stations does not shift samples.
Changing qualities cannot shift arrivals; changing schedules cannot shift any
products; changing one site's arrivals cannot shift another site's samples.
Changing one station's parameters cannot shift another station's calendar.

`verify_simulation.py` independently derives these identities and samples,
reusing only its own existing single-site generation audit for each point. It
reconstructs workstation calendars using standard-library code, imports no
simulation modules, and checks the new network provenance exactly (seeds are
identities, not tolerance-based physical measurements). The original raw-byte
scenario hash check is unchanged. Normal transport/plant/decision audit runs too.

## Historical fixed-layout acceptance checks (initial delay [0,3])

| Case | Observed |
|---|---|
| Seed 42 twice, actual CLI | scenario, event log, dispatch log, summary and final state byte-identical |
| Seed 42 products | 6: CP1 2, CP2 1, CP3 3; all remanufactured, 0 unfinished |
| Seed 43 products | 1 at CP2; all remanufactured, 0 unfinished |
| Seed 43 vs 42 calendars | All nine generated calendars changed |
| Fixed settings across seeds | Same coordinates, fleet, capacities and station durations |
| Both dynamic policies, same seed-42 resolved file | Identical scenario bytes/hash, both verifier PASS / exit 0 |
| Aware seed-42 distance / plant wait | 29.571782325925874 / 6.421501952454497 |
| Unaware seed-42 distance / plant wait | 25.099646370926294 / 8.484398804938206 |
| Forged window or schedule seed | Independent generation finding, FAIL / exit 1 |
| Standalone copied verifier | PASS, including network generation audit |
| Full regression | 145 tests pass: 136 existing + 9 new |

The seed-42 resolved scenario hash observed here is
`f461e6a2d6240410451aaa6f34bc68bcfcee247d1641208ea257c08792e33a3b`.
Generated demonstration files remain ignored under `results/three-site-review`;
test output is under ignored `work`. No results or virtual environment are committed.
A saved pilot was also rendered with the existing viewer's Agg backend at time 10.

## Reproduce in VS Code PowerShell

Use the Windows commands in README for initial runs, a different seed, paired
policy replay and visualization. Additional repeat/replay/test checks:

```powershell
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 42 --policy dynamic_quality_aware --output .\results\three-sites-42-repeat
& $thesisPython -m simulation --resolved-scenario .\results\three-sites-42\scenario.json --policy dynamic_quality_aware --output .\results\three-sites-42-replay
foreach ($file in @('scenario.json','events.jsonl','dispatch_decisions.jsonl','summary.json','final_state.json')) {
    [pscustomobject]@{
        File = $file
        RepeatMatches = (Get-FileHash ".\results\three-sites-42\$file").Hash -eq (Get-FileHash ".\results\three-sites-42-repeat\$file").Hash
        ReplayMatches = (Get-FileHash ".\results\three-sites-42\$file").Hash -eq (Get-FileHash ".\results\three-sites-42-replay\$file").Hash
    }
} # All True
& $thesisPython verify_simulation.py .\results\three-sites-43
& $thesisPython -m unittest discover -s tests -p test_network_generation.py -v
# 9 tests, OK
& $thesisPython -m unittest discover -s tests -p test_return_generation.py -v
# Original single-site generation/replay tests
& $thesisPython -m unittest discover -s tests -v
# 145 tests, OK (install existing optional visualization dependency for rendering tests)
```

The viewer needs the existing optional Matplotlib setup; no new dependency is
introduced. As before, use a desktop backend for a window or `--render image.png`
for an Agg snapshot. The Python equivalents ran here; PowerShell was not available.

## Function contracts

| Function / file | Caller; receives → returns; effects |
|---|---|
| `prepare_scenario()` / simulation/return_generation.py, changed | Existing CLI runner; config Path and integer seed → resolved dictionary/raw canonical bytes. Dispatches format 3 to network preparation; original format-2 path unchanged. Reads source, no output writing. |
| `network_stream_name()` / simulation/network_generation.py | Network preparation; category and entity IDs → unambiguous JSON-encoded stream name. Pure. |
| `collection_generation_proxy()` / same | Network validation/preparation; configuration, point, plant list → copied single-site adapter using the first supplied plant. Rebinds only copied AV starts for physical validation; never changes actual network settings. |
| `validate_network_generation()` / same | Network preparation; unresolved config → None/error. Checks physical schema, schedule declarations and every site's positively weighted routes against every plant using existing generation validation. No mutation/I/O. |
| `prepare_network_scenario()` / same | prepare_scenario; parsed config and integer seed → resolved dictionary/canonical bytes. Samples independent local RNGs, fills only copied windows/products/batches/provenance, validates resolved network. No file writes. |
| `validate_schedule_generation()` / simulation/schedule_generation.py | Schedule-settings adapter/tests; settings/horizon → None/error. Checks finite ordered bounds, time horizon and positive durations. Read only. |
| `station_schedule_settings()` / same | Network validation/preparation; config/station → merged settings or None for fixed windows. Rejects ambiguous declarations and validates parameters. No mutation. |
| `generate_windows()` / same | Network preparation/tests; validated parameters and isolated Random → windows. Advances only supplied schedule RNG, clips final closing, rejects non-advancing durations. |
| `run_once()` / simulation/runner.py, diagnostic changed | CLI/tests; source/output, seed/policy → summary. Same physical execution/export path; format-3 rejection text now correctly refers to both supported dynamic policies. Writes normal six run artifacts. |
| `audit_seeded_generation()` / verify_simulation.py, changed | verify_run or network audit's site adapters; saved scenario/findings → None. Dispatches new network provenance to audit_network_generation; original version-1 sampling audit retained. Appends findings only. |
| `audit_network_generation()` / same | Generation audit; saved network/findings → None. Independently derives seeds, strips site namespace for its own original sampling audit, checks origins/order/namespaces, reconstructs all generated windows, compares provenance exactly. No simulation imports or writes. |
| `verify_run()` / same, changed | Existing standalone CLI; run-folder Path → report. Includes new network generation audit before unchanged physical/decision checking; writes existing verification reports. |

`derive_stream_seed()`, `generate_return_batches()`, `generate_products()` and
`validate_generation_config()` remain unchanged. They are called via per-point
adapters; the original single-site inputs/outputs and stream protocol are preserved.

### Tests: tests/test_network_generation.py

unittest calls each test without arguments; all return None. Temporary sources,
runs and corrupted copies are automatically cleaned; repository inputs are not edited.

| Method | Inputs/return, callers and effects |
|---|---|
| `setUp()` | unittest → None; allocates cleaned temp root, pilot copy and counter. |
| `resolve()` | Tests; config/seed → resolved dictionary/bytes; writes only temp source, calls public prepare_scenario. |
| `run_cli()` | Tests; seed or resolved Path and policy → temp run Path; executes actual CLI, asserts success. |
| `verify_cli()` | Tests; run Path/expected exit → report; invokes independent child checker, creates temp reports. |
| `test_full_cli_repeat_replay_and_both_dynamic_policies()` | Repeats seed 42, replays frozen aware/unaware, checks byte identity/network dimensions/provenance/LF and four PASS reports. |
| `test_cli_seed_43_changes_samples_but_not_fleet_or_durations()` | Executes actual CLI for both seeds, checks changed samples and unchanged fleet/capacity/duration fields; verifies seed 43. |
| `test_quality_and_schedule_rules_cannot_shift_arrivals()` | Resolves edited quality weights/schedule durations, checks stream isolation without running physics. |
| `test_site_and_station_streams_are_independent_of_other_sites_and_order()` | Resolves CP1/one-station edits and reordered arrays; compares unaffected products/calendars/seed identities. |
| `test_calendar_algorithm_clipping_and_fixed_window_override()` | Checks hand-calculated [1,4],[6,9], last-window clipping and fixed-window station override. |
| `test_invalid_generation_is_rejected_before_creating_cli_output()` | Rejects invalid calendar/batch/quality/route/mixed settings and resolving already-resolved config; asserts no failed-run output created. |
| `test_independent_verifier_reconstructs_generated_schedules_and_seed_provenance()` | Forges a window and a schedule seed in separate copied runs, updates fingerprints, checks independent FAIL findings. |
| `test_network_verifier_remains_standalone_and_detects_product_forgery()` | Runs copied checker under isolated Python, then forges a sampled quality and checks generation.products violation. |
| `test_zero_arrivals_and_filtered_recycling_routes_reuse_legacy_rules()` | Resolves zero-rate CP1 and a recycling-only class without recyclers; checks no CP1 arrivals and no recycling samples. |
