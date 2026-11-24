# Optional seeded site coordinates

> Historical checkpoint-6 record. Paths and commands below describe that earlier
> version. For current entry points, use README.md and docs/architecture.md.

On `thesis-checkpoint-6`, unresolved format-three network configurations may add:

```json
"coordinate_generation": {"x_range": [0, 8], "y_range": [0, 8]}
```

When present, this replaces the coordinates of every collection point, plant and
configured recycler with two uniform samples, once during scenario preparation.
Source `coordinates` can be omitted when sampling is enabled; otherwise they are
used unchanged. The pilot retains its old coordinate fields as the fixed-layout
fallback when the setting is removed. Workstations have no separate spatial
samples: they belong to their parent plant. Fleet IDs, initial-site IDs, speeds,
payload, range, inventory capacities and processing durations are unchanged.

The pilot also now has `schedule_generation.initial_delay: [0,0]`: all nine generated
calendars first open at time zero. Later open/closed durations remain seeded.
Coordinate independence is checked with this same schedule setting in both the
fixed and generated variants; differences from the older pilot's initial delays
are intentional and separate from coordinate sampling.

## Streams, saved inputs and replay

Each site's coordinate seed is the existing SHA-256 derivation applied to the
master seed and the compact JSON string `['coordinates-v1', site_type, site_id]`.
Site types are `collection_point`, `plant` and `recycler`. One private Random is
created for each site; its first draw samples x and its second samples y. These
streams do not consult arrival rates, quality weights, calendars, site list order
or other sites. Existing return/quality/workstation stream identities are unchanged.

The resolved `scenario.json` stores the actual site coordinates and bounds,
plus `seed_provenance.coordinate_algorithm = 'site-type-id/uniform-xy-v1'` and
`seed_provenance.stream_seeds.coordinates`, grouped by site type and ID.
Generation version 2 retains its existing fixed-coordinate meaning when the
optional setting is absent. No optional geometry keys are added in that case.
Bounds must be finite, ordered and have a finite span; equal endpoints are valid.

The engine's existing initial-site lookup parks each AV at its configured
facility's generated coordinates. All route distances and travel times use that
geometry. The viewer reads the saved coordinates, and `--resolved-scenario` loads
the saved bytes without invoking any generator. The independent verifier derives
each site's stream and reconstructs its position itself, without simulation
imports. It also checks the new provenance exactly. The raw-byte scenario SHA-256
check remains unchanged.

A recycler regression check exposed an old verifier expectation that acceptance
would follow the order of a stop's delivery-list witness. Existing facility service
actually accepts eligible onboard products in recorded pickup order, including
products bound for a later visit to that same destination. The verifier now checks
that actual eligible onboard order. Disposition, ownership and destination checks
remain in place; physical unloading behavior was not changed.

## Observed results

| Check | Expected | Observed |
|---|---|---|
| Seed 42 twice | Identical coordinates/generated inputs | Identical scenario bytes and all four core output files |
| Seed-42 resolved replay | No regeneration, identical behavior | Scenario/events/dispatch/summary/final state all byte-identical |
| Seed 43 | Different layout | All six pilot site positions changed |
| Coordinate setting enabled versus omitted | Same products, batches, calendars | All identical with seed 42 and initial_delay [0,0] |
| Fixed-coordinate compatibility | Keep old behavior | Restoring the older pilot's initial_delay [0,3] also reproduces its original scenario hash exactly |
| Shared-input dynamic aware | Verifier PASS | PASS/0; 6 remanufactured, 0 unfinished, distance 32.371302379336925 |
| Shared-input dynamic unaware | Verifier PASS | PASS/0; 5 remanufactured, 1 unfinished at horizon 40, distance 37.248224555616716 |
| Normal pilot service | Pickup/admission at arrival when space permits | All 6 pickups and 6 admissions at matching arrival timestamps; no unload wait |
| Full one-slot variant | Keep load onboard until slot freed | Second part waits exactly 3 units; independent verifier PASS |
| Forged position / coordinate seed | Independent rejection | FAIL/1, generation.site_coordinates / generation.network_seed_provenance |
| Full regression | 149 existing + 9 new | All 158 tests passed |

For example, seed 42 generates CP1 `(0.8479551127498377,1.8214898633893597)` and
RF1 `(2.489783088179716,0.21120609999092288)`. CP1:P000001 is picked up at
`2.3491152646141633`, its collection-point arrival time, and admitted at
`4.648813701272502`, its facility arrival time. Travel remains positive and each
leg satisfies duration = distance / speed. Immediate unloading does not imply
immediate processing; busy/closed workstations still cause inventory waiting.

In the one-slot variant, coordinate bounds are [0,1] on both axes, only CP1 has
arrivals, batch size is 2 and every product needs [1,3,2]. The first AV arrives
at `0.6243414371204093`. CP1:P000001 enters inventory immediately; CP1:P000002
stays onboard. The first part holds its slot through WS1 (duration 1) and WS3
(duration 2). At `3.6243414371204095`, starting final WS2 frees the slot and the
waiting AV admits the second product at that exact time. Later blocked AVs retain
the existing arrival/ID priority. No engine scheduling or service delay rule changed.

## Exact PowerShell reproduction

Run in the project directory using your existing `$thesisPython`. These use new
output paths, so older saved fixed-layout runs remain intact.

```powershell
$root = Join-Path (Get-Location) 'results\coordinate-check'
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 42 --policy dynamic_quality_aware --output "$root\aware-42"
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 42 --policy dynamic_quality_aware --output "$root\repeat-42"
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 43 --policy dynamic_quality_aware --output "$root\aware-43"

# Both policies use one exact saved input file; aware replay also bypasses generation:
& $thesisPython -m simulation --resolved-scenario "$root\aware-42\scenario.json" --policy dynamic_quality_unaware --output "$root\unaware-42"
& $thesisPython -m simulation --resolved-scenario "$root\aware-42\scenario.json" --policy dynamic_quality_aware --output "$root\replay-42"
& $thesisPython verify_simulation.py "$root\aware-42"
$LASTEXITCODE # 0
& $thesisPython verify_simulation.py "$root\unaware-42"
$LASTEXITCODE # 0 (one unfinished product is correctly retained at the horizon)
& $thesisPython verify_simulation.py "$root\aware-43"
$LASTEXITCODE # 0

# Fixed-layout comparison: remove only coordinate_generation; leave initial_delay [0,0].
$fixed = Get-Content .\configs\three_site_pilot.json -Raw | ConvertFrom-Json
$fixed.PSObject.Properties.Remove('coordinate_generation')
[System.IO.File]::WriteAllText((Join-Path $root 'fixed-config.json'), ($fixed | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
& $thesisPython -m simulation --config "$root\fixed-config.json" --seed 42 --policy dynamic_quality_aware --output "$root\fixed-42"
& $thesisPython verify_simulation.py "$root\fixed-42"

# Inspect/compare generated inputs exactly with Python, avoiding JSON integer rounding in PowerShell:
@'
import json
from pathlib import Path
root = Path('results/coordinate-check')
def read(name):
    return json.loads((root / name / 'scenario.json').read_text(encoding='utf-8'))
def positions(scenario):
    return {(group, site['id']): site['coordinates']
            for group in ('collection_points', 'plants', 'recycling_facilities') for site in scenario[group]}
def windows(scenario):
    return {(plant['id'], station['id']): station['windows']
            for plant in scenario['plants'] for station in plant['workstations']}
a, b, c, fixed = map(read, ('aware-42', 'repeat-42', 'aware-43', 'fixed-42'))
assert positions(a) == positions(b)
assert positions(a) != positions(c)
assert a['products'] == fixed['products']
assert a['arrival_batches'] == fixed['arrival_batches']
assert windows(a) == windows(fixed)
assert all(station['windows'][0][0] == 0 for plant in a['plants'] for station in plant['workstations'])
for filename in ('scenario.json','events.jsonl','dispatch_decisions.jsonl','summary.json','final_state.json'):
    assert (root/'aware-42'/filename).read_bytes() == (root/'repeat-42'/filename).read_bytes() == (root/'replay-42'/filename).read_bytes()
assert (root/'aware-42/scenario.json').read_bytes() == (root/'unaware-42/scenario.json').read_bytes()
print('All coordinate, stream-isolation, repeat and replay checks passed')
print(positions(a))
'@ | & $thesisPython -

# Reproduce the positive-travel/full-inventory example:
$blocked = Get-Content .\configs\three_site_pilot.json -Raw | ConvertFrom-Json
$blocked.coordinate_generation.x_range = @(0,1)
$blocked.coordinate_generation.y_range = @(0,1)
foreach ($plant in $blocked.plants) { $plant.inventory_capacity = 1 }
foreach ($point in $blocked.collection_points) {
    $point.generation.batch_size = 2
    $point.quality_weights.Q132 = 1
    $point.quality_weights.Q3 = 0
    $point.quality_weights.Q21 = 0
    if ($point.id -ne 'CP1') { $point.generation.arrival_rate = 0 }
}
[System.IO.File]::WriteAllText((Join-Path $root 'blocked-config.json'), ($blocked | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
& $thesisPython -m simulation --config "$root\blocked-config.json" --seed 42 --policy dynamic_quality_unaware --output "$root\blocked-42"
& $thesisPython verify_simulation.py "$root\blocked-42"
Get-Content "$root\blocked-42\events.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.event_type -in @('unload_wait','inventory_slot_release','plant_admission') } | Format-Table sequence,time,event_type,product_id,vehicle_id

& $thesisPython -m simulation.visualization "$root\aware-42" --time 10
# Optional headless screenshot (selects Agg in the viewer process):
& $thesisPython -m simulation.visualization "$root\aware-42" --time 10 --render "$root\layout-42.png"
& $thesisPython -m unittest discover -s tests -p test_coordinate_generation.py -v
# 9 tests, OK
& $thesisPython -m unittest discover -s tests -v
# 158 tests, OK
```

Python equivalents were executed here; PowerShell itself was unavailable. Existing
optional Matplotlib setup is sufficient for visualization; no new dependency was
introduced. Demonstrations are in ignored `results/coordinate-review`, tests in
ignored `work`. No generated result or virtual environment is part of the change.

## Function contracts and tests

| Function | Caller; input → return; effects |
|---|---|
| `validate_coordinate_generation()` / simulation/coordinate_generation.py | Network validation/sampling; settings → None/error. Checks both finite ordered axis ranges and spans, without mutation. |
| `derive_coordinate_seed()` / same | Coordinate sampling/tests; master seed, site type, ID → integer. Pure stable SHA-256 stream derivation through the existing helper. |
| `generate_site_coordinates()` / same | Network preparation; copied scenario/master seed → per-type seed dictionaries. Mutates only copied site coordinates, with x then y draws from each private RNG. |
| `validate_network_generation()` / simulation/network_generation.py, changed | Network preparation; unresolved input → None/error. Uses lower-bound placeholders in a private validation copy when coordinates will be sampled. Adapts point/plant/recycler geometry to the existing physical/quality validators. Source unchanged. |
| `prepare_network_scenario()` / same, changed | Existing prepare_scenario; config/seed → resolved dictionary/canonical bytes. Samples geometry once before other independent generators, records optional coordinate provenance and validates final physical inputs. No file I/O. |
| `audit_generated_coordinates()` / verify_simulation.py | Network audit; saved scenario/findings → expected coordinate stream seeds. Independently derives seeds/XY, appends input violations, imports no simulator code. |
| `audit_network_generation()` / same, changed | Existing generation dispatch; scenario/findings → None. Also checks geometry and exact optional coordinate provenance; fixed-layout provenance remains unchanged. |
| `DynamicAudit.consume_event()` / same, changed | Independent replay; event → None. Recycler-arrival follow-ups now use eligible onboard order, matching actual facility service. Retains all existing physical/ownership/disposition checks. |

`tests/test_coordinate_generation.py` adds nine tests. unittest calls setUp and
each test without arguments; all tests return None and use cleaned temporary folders.

| Helper/test | Contract and check |
|---|---|
| `site_positions()` | Tests; scenario → read-only type/ID map for geometry comparisons. |
| `workstation_windows()` | Tests; scenario → read-only plant/station window map. |
| `setUp()` | Allocates cleaned scratch space, pilot copy and case counter. |
| `resolve()` | Tests; config/seed → scenario/bytes; writes temp source and calls public preparation. |
| `run_cli()` | Tests; seed/config or resolved path/policy → folder, scenario, events; executes actual CLI and writes only temp run artifacts. |
| `verify_cli()` | Tests; folder/expected exit → report; invokes independent child checker, writes reports and asserts status. |
| `test_cli_same_seed_layout_repeat_different_seed_and_both_policy_replays()` | Repeats seed 42, changes to 43, replays both policies and compares saved bytes/initial windows and PASS results. |
| `test_enabling_coordinates_keeps_returns_qualities_and_windows_identical()` | Toggles optional geometry and checks stream isolation/fixed inputs; pins exact old fixed-layout scenario hash when old delays are restored. |
| `test_coordinate_stream_identity_and_axis_bounds()` | Checks seed/type/ID separation, bounds and degenerate-axis behavior. |
| `test_stream_isolation_in_both_directions_and_site_order()` | Changes bounds, other generator rules and list order/adds recycler; checks unaffected streams and per-site positions. |
| `test_recyclers_missing_source_coordinates_and_initial_vehicle_positions()` | Generates all site kinds without placeholders, starts an AV at a generated recycler, checks viewer/engine initial positions and verifies actual recycling. |
| `test_invalid_coordinate_settings_rejected_before_output_creation()` | Rejects missing/reversed/nonfinite/overflowing ranges and asserts CLI creates no partial output. |
| `test_independent_verifier_detects_false_positions_and_coordinate_seed()` | Edits separate run copies and updates raw hashes to prove independent position/seed audit detects forgeries. |
| `test_pickups_and_space_available_admissions_match_arrival_timestamps()` | Compares all pilot pickups/admissions to actual arrival records and all positive leg durations to distance/speed; verifies run. |
| `test_full_inventory_still_keeps_load_onboard_until_slot_freed()` | Uses one-slot variant, checks the three-unit held-slot wait and same-time retry/admission with positive travel; verifies run. |

The prior network test `test_cli_seed_43_changes_samples_but_not_fleet_or_durations()`
was updated for the pilot's new stochastic geometry: it continues to check fixed
AV definitions, capacities, non-spatial collection settings and processing durations.
