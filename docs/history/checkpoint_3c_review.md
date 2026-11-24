# Checkpoint 3c — optional vehicle range

Created `thesis-checkpoint-3c` from `thesis-checkpoint-3b` commit
`027ef4a051ce3434578b3fc5c95de28e7b4421f6`. Implemented checkpoint 3c only.
Source, tests and documentation are maintained on `thesis-checkpoint-3c`.

## Decisions and scope

Version-two AVs accept a positive finite `maximum_range` or `null` for unlimited.
Finite AVs begin at a configured plant/recycler with full allowance. Their
runtime records add `remaining_range`, actual `position`, and
`leg_distance_accounted`. The last field prevents counting a leg prefix again
when another event advances the clock. Unlimited AVs retain their previous
record shape and skip range checks/resets. Legacy version-one input remains
unchanged.

One fixed trip per AV remains the scope, but its stops may now include explicit
facility visits before/after at most one collection pickup. Every plan ends at
a configured facility. The final stop is the committed delivery destination;
intermediate facilities are visits, retaining onboard products. A plan without
a collection stop is an empty reposition/reset trip and assigns no products.
No dynamic routing or mid-leg replanning is introduced. Custom initial positions
away from facilities and multiple plants/collection points remain unsupported.

The engine checks the whole route at the scheduled departure opportunity,
BEFORE assigning a product or starting a leg. It sums distances until each
facility arrival, then evaluates the next segment with a reset allowance.
It also checks the remaining suffix immediately before each finite leg. An
unreachable later segment blocks the first departure even if the first leg or
first reset facility is reachable. Range comparisons allow absolute error
`1e-9`; saved numeric comparisons retain relative/absolute `1e-9`.

**Rejection is a correct simulation outcome, not an invocation failure.** A
range-5 run exits 0 after logging `range_plan_check` and `trip_rejected` with
required 6/available 5, leaving the request unassigned at CP1. Its verifier
returns PASS/0 because it reconstructs and confirms the justified rejection.
Malformed input values still fail CLI validation.

Finite motion is integrated on each clock advance from actual leg geometry,
speed and elapsed time, including the final partial interval to the horizon.
Arrival records preserve remaining allowance BEFORE a reset. A separate
`range_reset` immediately follows arrival at a facility, before delivery or
waiting. CP1 never resets range. Empty visits and visits to a recycler that
cannot process the carried remanufacturable part still reset range.

## Hand-checkable examples and observed results

`checkpoint_3c_range_6.json`: RF1 `(0,0)`, CP1 `(3,0)`, AV1 speed/payload 1,
range 6, departure 0, stops CP1/RF1, one released P1 requiring operation 1.
WS1 takes 1 in `[0,8)`, horizon 8.

| Expected time | Actual time | Sequence | Event / allowance |
| --- | --- | --- | --- |
| 0 | 0 | 1 | Release P1 at CP1 |
| 0 | 0 | 2 | Full-plan check: required 6, available 6 |
| 0 | 0 | 3–4 | Assignment and departure RF1→CP1 with 6 |
| 3 | 3 | 5 | CP1 arrival with 3; no reset |
| 3 | 3 | 6–7 | Pickup and departure CP1→RF1 with 3 |
| 6 | 6 | 8 | RF1 arrival with 0 |
| 6 | 6 | 9 | Reset 0→6 while P1 is still onboard |
| 6 | 6 | 10–11 | Admission, then processing |
| 7 | 7 | 12–13 | Operation completion and remanufactured |
| 8 | 8 | 14 | Horizon, actual distance 6, allowance 6 |

| Example/check | Expected | Observed |
| --- | --- | --- |
| Range 6 | Trip completes, 3 at CP1, 0 at RF1, reset 6 | Exact match; verifier PASS/0 |
| Range 5 | Reject before departure, required 6 / available 5 | Sequence 3 at time 0; distance 0, P1 unassigned; verifier PASS/0 |
| Unlimited | Trip completes without range checks/resets | Distance 6, remanufactured 1; verifier PASS/0 |
| Empty recycler visit | Facility reset despite no delivery | At RC1/time 4: 2→6, load []; verifier PASS/0 |
| Mid-leg horizon 4 | Only 4 distance travelled, remaining 2, position (2,0) | Exact match; verifier PASS/0 |
| Insert CP1 reset | FAIL/1, facility-only rule | `range.reset_facility`, sequence 6/time 3, expected [RF1], observed CP1 |

`checkpoint_3c_empty_recycler.json` has no products and RC1 `(4,0)`. Its fixed
trip is RF1→RC1→CP1→RF1. The two segments between facility arrivals are 4 and 4,
so range 6 is sufficient although total distance is 8. At time 4 the AV arrives
at RC1 with 2 and resets to 6 with empty payload. It departs immediately, reaches
CP1 at 5 with 5, then RF1 at 8 with 2 and resets to 6. No recycling acceptance
occurs. No initial reset event is needed: the AV starts full.

`checkpoint_3c_mid_leg.json` stops at 4. Outbound travel consumes 3 by time 3;
return travel consumes only 1 by time 4. P1 stays onboard. The saved active leg
still has planned arrival 6, but `remaining_range` is 2, position is `[2,0]`,
actual distance 4, and its accounted prefix is 1. There is no premature reset.

The tests also show a range-4 plan RF1→CP1→RC1 `(2,0)`→RF1. Its segments are
4 and 2. The recycler resets the AV at time 4 while carrying P1, but does not
accept P1; P1 remains committed to RF1. A separate blocked-plant test shows AV2
resetting at arrival time 5, before `unload_wait`, not at its eventual admission 8.

## Tests and compatibility

All 59 tests pass: the existing 44 and 15 new range tests. One existing test was
updated because rejecting every finite range is obsolete: in
`tests/test_inventory.py`, `test_invalid_capacity_and_later_features_rejected`
is now `test_invalid_capacity_and_range_rejected` and checks maximum 0 rather
than valid maximum 7. Existing capacity checks remain. No other existing test
or saved fixture was changed.

The independent verifier imports no simulation code. It has its own segment
calculator, incremental motion integration, finite-state initialization and
reset validation. It checks plan feasibility/rejection, actual allowances,
fixed stops, mandatory facility resets and complete final state. A false reset
at CP1 is diagnosed as a reset-site violation before it can alter replay state.
Existing SHA-256 byte checks are unchanged. The new six-file fixture contains
an LF-pinned scenario and is verified with an isolated copy of the standalone
verifier, preserving all six input files byte-for-byte.

## Exact PowerShell reproduction

Run from the repository root with these changes present and your existing venv.
Python commands and equivalent log edits ran in the implementation environment;
PowerShell itself is unavailable there. These commands target Windows
PowerShell 5.1 and PowerShell 7.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path

# Existing 44 tests:
& $thesisPython -m unittest discover -s tests -p test_checkpoint_1.py -v
& $thesisPython -m unittest discover -s tests -p test_verifier.py -v
& $thesisPython -m unittest discover -s tests -p test_inventory.py -v
& $thesisPython -m unittest discover -s tests -p test_return_generation.py -v
# New 15, then all 59:
& $thesisPython -m unittest discover -s tests -p test_vehicle_range.py -v
& $thesisPython -m unittest discover -s tests -v

$demoRoot = Join-Path .\results ("checkpoint-3c-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
foreach ($case in @('range_6', 'range_5', 'unlimited', 'empty_recycler', 'mid_leg')) {
    $config = Join-Path .\configs ("checkpoint_3c_" + $case + ".json")
    $run = Join-Path $demoRoot $case
    & $thesisPython -m simulation --resolved-scenario $config --output $run
    "Simulation exit: $LASTEXITCODE"           # 0, including the correctly rejected trip
    & $thesisPython .\verify_simulation.py $run
    "Verifier exit: $LASTEXITCODE"             # 0 for all five
}

$valid = Join-Path $demoRoot range_6
$rejected = Join-Path $demoRoot range_5
$mid = Join-Path $demoRoot mid_leg
$empty = Join-Path $demoRoot empty_recycler
Get-Content (Join-Path $valid events.jsonl)
Get-Content (Join-Path $rejected events.jsonl) | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.event_type -eq 'trip_rejected' } | Format-List
Get-Content (Join-Path $empty events.jsonl) | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.event_type -eq 'range_reset' } | Format-List
$midState = Get-Content (Join-Path $mid final_state.json) -Raw | ConvertFrom-Json
$midState.vehicles.AV1 | Format-List

# Add a false reset immediately after CP1 arrival in an independent copy:
$invalid = Join-Path $demoRoot false-cp-reset
Copy-Item $valid $invalid -Recurse
$eventPath = Join-Path $invalid events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$falseReset = [pscustomobject]@{
    sequence = 0
    time = 3
    event_type = 'range_reset'
    vehicle_id = 'AV1'
    facility_id = 'CP1'
    range_before = 3
    range_after = 6
    load = @()
}
$edited = New-Object System.Collections.ArrayList
foreach ($event in $events) {
    [void]$edited.Add($event)
    if ($event.event_type -eq 'leg_arrival' -and $event.target -eq 'CP1') {
        [void]$edited.Add($falseReset)
    }
}
for ($index = 0; $index -lt $edited.Count; $index++) {
    $edited[$index].sequence = $index + 1
}
$lines = @($edited | ForEach-Object { $_ | ConvertTo-Json -Depth 40 -Compress })
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $invalid
"False-reset verifier exit: $LASTEXITCODE"     # 1
Get-Content (Join-Path $invalid verification.txt)

git check-attr text eol -- tests/fixtures/checkpoint_3c/scenario.json
# text: set; eol: lf
```

## Walk the range-6 calls

1. Unchanged CLI `main()` calls `run_once()`, `load_scenario()` and the version-two
   validator. `build_inventory_state()` creates AV1 at RF1 with remaining 6,
   actual position `[0,0]` and accounted prefix 0.
2. The engine releases P1 at 0. `dispatch_inventory_trip()` calls
   `evaluate_fixed_trip_range(scenario, state, trip)`: RF1→CP1 is 3, CP1→RF1 is
   another 3, and CP1 is not a reset. One segment requires 6, available 6. It
   logs the check, records a quality-unaware decision, assigns P1 and returns True.
3. `depart_inventory_leg(..., RF1, CP1, stop_index=0)` rechecks the remaining
   plan, logs allowance 6 at departure, and returns arrival time 3.
4. Before clock 3 events, `advance_vehicle_travel(..., time=3)` computes actual
   covered distance 3 minus already-accounted 0. It adds 3 distance, subtracts
   3 allowance, stores position `[3,0]` and accounted prefix 3.
5. `arrive_inventory_leg(scenario, state, 'AV1')` logs CP1 arrival with 3,
   clears the active leg/accounted prefix, and calls `reset_vehicle_range()`.
   That helper sees a collection point and returns without reset or event.
6. Pickup moves P1 onboard. `depart_inventory_leg(..., CP1, RF1, stop_index=1)`
   rechecks the suffix: required 3, available 3. Arrival is scheduled at 6.
7. Before clock 6 events, motion integration subtracts the return leg's actual
   3, leaving 0 and cumulative distance 6. Arrival logs 0 before reset.
8. `reset_vehicle_range()` now sees RF1, stores remaining 6 and logs sequence 9
   while load is still `[P1]`. Only then does plant admission remove P1.
9. Processing completes at 7. Horizon 8 adds no vehicle distance. The runner
   saves range 6/position `[0,0]`/distance 6. Independent replay calculates the
   same geometry and allowance changes rather than trusting the logged values.

## Every new or changed simulation function

All functions have type hints and short docstrings. `Record` is a JSON-shaped
record. No transition helper writes files; it changes the passed state and logs
in memory, which the unchanged runner later writes as six artifacts.

| File / function | Caller; receives → returns | State changes and rules |
| --- | --- | --- |
| `simulation/inventory_config.py` / `validate_inventory_scenario()` | Version-two loader or seeded resolver; scenario → None | Accepts positive finite/null maxima and configured facility starts. Checks fixed-stop references, at most one CP pickup, final facility and existing capacities/calendars. No mutation. |
| `simulation/models.py` / `build_inventory_state()` | Engine; scenario → new InventoryRunState | Adds remaining range, position and accounted prefix only for finite AVs; full allowance at initial facility. Existing unlimited state unchanged. |
| `simulation/inventory_engine.py` / `run_inventory_simulation()` | Runner; resolved scenario → final state | Tracks next stop per AV, advances actual finite motion before each batch/horizon, follows configured facility visits, delivers only at final stop, honors dispatch rejection and prevents duplicate horizon distance. |
| `simulation/routing.py` / `dispatch_inventory_trip()` | Engine at scheduled departure; scenario/state/trip → bool | Checks whole plan first, logs finite feasibility, records existing boundary decision with optional rejection, returns False without assignment/motion on failure. Returns True after feasible assignments (none for a visit-only trip). |
| `simulation/transport.py` / `depart_inventory_leg()` | Engine before every leg; scenario/state/vehicle/origin/target/stop index → arrival time | Rechecks finite remaining suffix using current position/allowance, stores departure allowance and clears accounted prefix, then starts/logs leg. Unlimited behavior unchanged. |
| `simulation/transport.py` / `arrive_inventory_leg()` | Engine at due arrival; scenario/state/vehicle ID → None | Logs pre-reset allowance, updates site/position and clears leg/prefix; finite distance was already integrated. Unlimited distance is added here as before. Calls facility-reset helper before service. Signature now takes scenario explicitly. |
| `simulation/vehicle_range.py` / `network_site()` | Engine/planner; scenario/site ID → site record | Looks up plant, recycler or CP coordinates; no mutation. |
| `simulation/vehicle_range.py` / `evaluate_fixed_trip_range()` | Dispatcher and leg-start guard; scenario/state/trip/optional suffix index → feasibility/segments/optional rejection record | Independently of loading, sums actual-position distances until each facility, then resets forecast allowance. Returns first failing segment diagnostic. Unlimited returns feasible without range geometry/checks. No mutation. |
| `simulation/vehicle_range.py` / `advance_vehicle_travel()` | Engine on each prospective clock advance; scenario/state/target time → None | Adds only new leg-prefix distance, consumes allowance, checks nonnegative budget and interpolates actual position for finite active AVs. Does not advance simulation clock itself. |
| `simulation/vehicle_range.py` / `reset_vehicle_range()` | Arrival handler; scenario/state/vehicle ID → None | For finite facility arrivals, records old allowance, resets full and logs load/site/time. CP or unlimited returns without changes. |

The existing range-free version-one engine and CLI/runner functions are
unchanged. Existing plant settling, FIFO and waiting-AV priority functions are
unchanged. Their contracts remain in the earlier checkpoint reviews.

## Every new or changed independent-verifier function

All are in `verify_simulation.py`. None imports simulation/transport/range code.

| Function/method | Caller; receives → returns | State/files and checks |
| --- | --- | --- |
| `add_finding()` | Comparison/reader/coordinator helpers; diagnostic details → None | Adds facility/recycler IDs to automatically extracted event context. Existing rule/time/sequence/expected/observed fields unchanged. |
| `validate_inventory_inputs()` | Supported-format validator; scenario → None | Own validation of positive/null range, facility starts and expanded fixed stops. No input mutation. |
| `InventoryAudit.__init__()` | Coordinator; scenario/findings → audit object | Builds independent site map, checked-plan storage and finite range/position/prefix state from inputs. |
| `InventoryAudit.evaluate_range_plan()` | Plan/departure checks; AV ID/optional suffix index → feasibility record | Independently sums geometry and resets forecast budget only at configured facilities. No state mutation. |
| `InventoryAudit.advance_range_travel()` | Replay before consuming each event; time/context event → None | Independently integrates actual finite movement, remaining budget, position and prefix, requiring legal travelled increments. |
| `InventoryAudit.consume_range_event()` | General event handler; event → None | Checks single full-plan evidence/time, justified no-departure rejection, or mandatory facility reset with pre/post allowance and actual load. Saves checked plans or resets own state. |
| `InventoryAudit.capture_dispatch()` | Event handler on first assignment/departure/rejection; event/AV ID → None | Requires prior finite plan evidence; computes zero selected requests and expected rejection when infeasible, or existing eligible selection. Queues proper next events and removes completed departure opportunity from deadlines. |
| `InventoryAudit.consume_event()` | Replay; observed event → None | Rejects reset at CP before applying it, requires mandatory arrival reset, handles arbitrary fixed leg indexes and intermediate visits, checks departure suffix and arrival allowances. Advances only own physical state. |
| `InventoryAudit.replay()` | Coordinator; observed events → reconstructed final state | Integrates finite motion before event checks; avoids old horizon double-count while preserving unlimited accounting. Existing chronology/deadline rules remain. |
| `verify_run()` | Standalone CLI; folder → report | Coverage text now includes fixed-plan range. Existing hash, seeded audit, artifact parsing, report writing and exit precedence unchanged. Writes only verification.json/txt via unchanged writer. |

At sequence 5/CP1 arrival, the audit independently computes 3 and expects no
reset. An inserted reset is rejected as `range.reset_facility` with expected
`['RF1']`, observed `CP1`, before changing the reconstructed allowance. At
sequence 8/RF1 arrival, the independently computed allowance is 0 and a mandatory
range-reset follow-up is queued. It must be consumed before admission; its
range-before 0/range-after 6 and onboard load are checked against own state.

## Every new or changed test/helper

All new methods below are in `tests/test_vehicle_range.py`. unittest calls
`setUp()` before each test; each test receives only self and returns None or
raises an assertion failure. All generated and edited files are temporary and
cleaned automatically. The six-file saved fixture remains unchanged.

| Method | Caller, inputs/return, state/files and checks |
| --- | --- |
| `setUp()` | unittest; self → None. Creates scratch root/cleanup, loads fresh scenario and initializes numbering. |
| `run_case()` | Tests; optional scenario → folder/events/final state. Writes temporary config and invokes existing runner for one resolved run. |
| `verify_case()` | Tests; folder/expected exit → parsed report. Executes standalone verifier and checks process/report exit; only reports written by verifier. |
| `save_events()` | Mutation tests; folder/edited event records → None. Renumbers and writes only temporary events.jsonl using UTF-8/LF. |
| `test_range_six_journey_and_reset_order()` | Checks 6→3→0→6, event sequences, reset before admission, final distance/position and verifier PASS. |
| `test_range_five_rejected_before_assignment_or_departure()` | Checks required 6/available 5, no assignment/pickup/departure, unchanged full allowance and request ownership, justified-rejection PASS. |
| `test_unlimited_range_skips_checks_and_completes()` | Sets null range, requires no checks/resets, confirms completion/distance and verifies. |
| `test_empty_recycler_visit_resets_before_next_leg()` | Uses the empty RC1 plan; checks time-4 2→6 with empty load, next departure with 6, no acceptance, total distance 8. |
| `test_false_collection_point_reset_fails()` | Inserts a CP reset after arrival, renumbers and requires rule/time/sequence/expected/observed diagnostic. |
| `test_mid_leg_range_and_position_use_only_actual_motion()` | Checks outbound horizon 1.5 and inbound horizon 4, with an extra time-2 release to exercise repeated clock integration and no double counting. |
| `test_facility_reset_precedes_blocked_unloading()` | Adds finite range to the 3a fixture; requires AV2 reset at arrival 5 before waiting, not at unload 8. |
| `test_intermediate_recycler_resets_carried_remanufacturable_product()` | Runs segments 4/2 with maximum 4; checks loaded recycler reset and retained RF1 commitment without acceptance. |
| `test_invalid_range_and_plan_structure_rejected()` | Rejects 0/negative/bool/nonfinite maxima, missing final facility, repeated CP, unknown stop and initial CP. Only local inputs change. |
| `test_heterogeneous_finite_and_unlimited_vehicles()` | Runs one of each together; checks independent allowance, total distance and no unlimited reset records. |
| `test_missing_reset_and_forged_allowances_fail()` | Deletes reset or forges arrival/exported allowance in separate copies; all must FAIL. |
| `test_later_infeasible_segment_blocks_the_first_departure()` | Makes first reset reachable but later segment require 5 with range 4; checks no travel and verifier-confirmed rejection. |
| `test_initial_recycler_start_has_full_allowance()` | Starts at RC1 with range 4; checks actual departure origin/full allowance, distance 4 and PASS. |
| `test_seeded_finite_range_replay_matches_events()` | Adds finite range to 3b generated inputs; checks replay scenario/event/summary/final-state bytes identical and verifies both. |
| `test_frozen_fixture_hash_lf_and_isolated_verifier()` | Checks LF/hash, runs only a copied verifier in isolated Python and preserves all six evidence bytes. |

Changed existing method in `tests/test_inventory.py`:
`test_invalid_capacity_and_range_rejected()` is called by unittest with self and
returns None. It preserves capacity 0/-1/1.5/True rejection, and now checks invalid
range 0 rather than rejecting newly supported valid range 7. It changes only
local scenario copies and writes no files. Its old name is recorded above.

## Changed files

- `.gitattributes`, `README.md`.
- Five new configs: `configs/checkpoint_3c_range_6.json`, `checkpoint_3c_range_5.json`,
  `checkpoint_3c_unlimited.json`, `checkpoint_3c_empty_recycler.json`,
  `checkpoint_3c_mid_leg.json`.
- `simulation/inventory_config.py`, `inventory_engine.py`, `models.py`,
  `routing.py`, `transport.py`; new `simulation/vehicle_range.py`.
- `verify_simulation.py`.
- `tests/test_inventory.py`; new `tests/test_vehicle_range.py`.
- Six new evidence files under `tests/fixtures/checkpoint_3c/`: scenario.json,
  run_manifest.json, events.jsonl, dispatch_decisions.jsonl, summary.json,
  final_state.json. Its scenario is pinned to LF.
- New `docs/checkpoint_3c_review.md`.

Generated results, virtual environment and scratch files remain ignored.
Stopped after checkpoint 3c for review.
