# Checkpoint 3a — finite shared inventory

Created `thesis-checkpoint-3a` from `thesis-checkpoint-2` at
`9028a3fc4407a854a1c1a9a46453dabb27099826`, including the LF fix. Implemented only
checkpoint 3a. Source, tests and this review are maintained on
`thesis-checkpoint-3a`; generated results remain ignored.

## What to review

The one-slot example uses RF1 `(0,0)`, CP1 `(2,0)`, two unit-speed, capacity-one
AVs, and two released products. AV1 departs at 0 and returns at 4 with P1;
AV2 departs at 1 and returns at 5 with P2. P1 requires `[1,3]`, P2 `[1]`.
WS1 takes 2 units in `[4,6)` and `[9,11)`; WS3 takes 3 in `[8,11)`.
Horizon is 12. Both products finish at 11.

The multi-product scenario and outputs use version 2. Version 1 stays readable
and retains its original single-product, unlimited-inventory contract, including
its five tests. Version 2 uses `products`, `vehicles`, `trips`, per-entity final
state and explicit hold/unload records. It accepts finite positive integer or
unlimited inventory, one plant/collection point and at most one fixed trip per
vehicle. These limits avoid adding later routing-policy features. There is no
random generation, recycling, finite range, experiment program or visualization.

At dispatch, released unassigned requests are selected by release time then
product ID, without quality/operation information crossing the policy boundary.
A trip may be empty. Multiple products can share a vehicle within payload.
AV admission priority is arrival time then vehicle ID. Every waiting AV retains
its original plant arrival time even after partial unloading. Workstations select
eligible products by latest inventory-entry time then product ID, rather than
letting a product for another machine block the inventory queue.

`inventory` maps physically waiting product IDs to their most recent entry time.
`held_slots` contains only products on intermediate operations. Capacity is
`len(inventory) + len(held_slots)`. A start removes physical inventory; an
intermediate start adds a hold atomically and logs it next. Intermediate
completion replaces the hold with physical inventory. A final start needs no
return hold, so the vacated slot is available immediately. No hold is counted as
physical stock or inventory waiting. Onboard waiting-to-unload time is separate
from plant inventory waiting.

The engine settles completions and releases/arrivals before workstation starts,
then dispatch. It processes immediate actions until none remain; after each
station start it retries AV admission before another station start. This lets an
AV use capacity freed at that exact timestamp. Station IDs provide deterministic
iteration for simultaneous independent starts/completions. Adjacent calendar
windows are merged for fit checks without mutating the saved scenario bytes.

The independent verifier's version-two path reads observed events and advances
its own dictionaries only after checking each event. It does not import or call
any simulation code. It checks capacity before admission, mandatory hold/return/
release/retry records, priority, FIFO eligibility, geometry, operation order,
calendars, deadlines, physical conservation, horizon retention, policy input,
and complete exported state/totals. It rejects time advances past an enabled
start/admission or missing due action. It checks settled timestamps, not the
transient split between a completion record and its immediate inventory return.

Byte-for-byte SHA-256 remains unchanged. `scenario.json` is copied as original
bytes. Both hashed saved scenario fixtures are pinned to LF in `.gitattributes`.
The new checked-in six-file fixture is intentional test evidence, not a results
folder. Standalone isolated verification and input-byte preservation are tested.
The same report files, tolerance `1e-9`, and exit precedence remain: violation 1
beats incomplete 2; a complete valid run returns 0.

## Expected and actual sequence

| Expected time | Actual time | Sequence | Actual record |
| --- | --- | --- | --- |
| 4 | 4 | 14 | Admit P1: waiting 1, held 0 |
| 4 | 4 | 15 | Start P1 at WS1, operation index 0, end 6 |
| 4 | 4 | 16 | Hold P1's slot: waiting 0, held 1 |
| 5 | 5 | 17 | AV2 arrives carrying P2 |
| 5 | 5 | 18 | Unload blocked: P2 stays onboard, waiting 0, held 1 |
| 6 | 6 | 19 | Complete P1's WS1 operation |
| 6 | 6 | 20 | Return P1 to inventory, replacing its hold: waiting 1, held 0 |
| 8 | 8 | 21 | Start P1's final operation at WS3, end 11 |
| 8 | 8 | 22 | Log slot release: waiting 0, held 0 |
| 8 | 8 | 23 | AV2 retries using original arrival time 5 |
| 8 | 8 | 24 | Admit P2: waiting 1, held 0 |
| 9 | 9 | 25–26 | Start P2's only/final operation; release its slot |
| 11 | 11 | 27–30 | Complete P2 and P1 and record both as remanufactured |
| 12 | 12 | 31 | Horizon: no stock, holds, waiting AVs or unfinished work |

Final summary: generated 2, remanufactured 2, recycled 0, unfinished 0,
travel distance 8, inventory wait 3, waiting to unload 3. Inventory wait comprises
P1 `[6,8)` = 2 and P2 `[8,9)` = 1. AV2 waits onboard over `[5,8)` = 3.

Valid run: expected PASS/0; observed PASS/0. In a copied log, P2 admission was
moved to time 5 after the unload-wait record, the original retry/admission were
removed, and sequences renumbered. Expected FAIL/1; observed FAIL/1:

```text
rule: inventory.capacity
sequence: 19
time: 5
IDs: P2, AV2, RF1
expected: waiting + held <= 1
observed: waiting_after_admission=1, held_slots=1, attempted_occupancy=2
```

This mutation isolates capacity: it preserves chronological order and product
ownership rather than merely changing a late record's timestamp out of order.
Reports stop unsafe replay at the violation and do not claim verified final
totals for the corrupted run.

All original 17 tests and 13 new tests pass: 30 total.

## PowerShell reproduction

From the repository root with these changes present, retain your `$thesisPython`
or point it at the existing environment. No dependency installation is needed.
These commands use PowerShell 5.1/7-compatible syntax. The Python commands and
equivalent log edits ran in the cloud; PowerShell itself is unavailable here.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path

# Existing 17 tests, new 13 tests, then all 30:
& $thesisPython -m unittest discover -s tests -p test_checkpoint_1.py -v
& $thesisPython -m unittest discover -s tests -p test_verifier.py -v
& $thesisPython -m unittest discover -s tests -p test_inventory.py -v
& $thesisPython -m unittest discover -s tests -v

$demoRoot = Join-Path .\results ("checkpoint-3a-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$validRun = Join-Path $demoRoot valid
$invalidRun = Join-Path $demoRoot early-admission

& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_3a.json --output $validRun
& $thesisPython .\verify_simulation.py $validRun
"Valid verifier exit: $LASTEXITCODE"          # 0
Get-Content (Join-Path $validRun events.jsonl)
Get-Content (Join-Path $validRun final_state.json)
Get-Content (Join-Path $validRun verification.txt)

Copy-Item $validRun $invalidRun -Recurse
$eventPath = Join-Path $invalidRun events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$admission = $events | Where-Object { $_.event_type -eq 'plant_admission' -and $_.product_id -eq 'P2' }
$admission.time = 5
$admission.waiting_count = 1
$admission.held_count = 1
$edited = New-Object System.Collections.ArrayList
foreach ($event in $events) {
    if ($event.event_type -eq 'unload_retry') { continue }
    if ($event.event_type -eq 'plant_admission' -and $event.product_id -eq 'P2') { continue }
    [void]$edited.Add($event)
    if ($event.event_type -eq 'unload_wait') { [void]$edited.Add($admission) }
}
for ($index = 0; $index -lt $edited.Count; $index++) {
    $edited[$index].sequence = $index + 1
}
$lines = @($edited | ForEach-Object { $_ | ConvertTo-Json -Depth 30 -Compress })
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $invalidRun
"Invalid verifier exit: $LASTEXITCODE"        # 1
Get-Content (Join-Path $invalidRun verification.txt)

git check-attr text eol -- tests/fixtures/checkpoint_3a/scenario.json
# Expected: text: set; eol: lf
```

## Function review — simulation path

Each row gives the caller, received arguments, returned value and changes. All
new/changed functions have type hints and short docstrings. `Record` means a
JSON-shaped dictionary; no parser/state/checking implementation is shared with
the verifier. Lambda key expressions only encode the priorities stated below.

| File / function | Caller; receives → returns | State/files and rules |
| --- | --- | --- |
| `simulation/__main__.py` / `main()` | Python module entry; CLI → exit integer | Calls runner and prints summary. Only the description changed to cover both formats. Existing error/argument behavior remains. |
| `simulation/config.py` / `load_scenario()` | Runner; input path → scenario and original bytes | Reads and validates; no input mutation or writes. Docstring now covers both versions. |
| `simulation/config.py` / `validate_scenario()` | Loader/tests; scenario → None | Delegates format 2 to inventory validator; preserves all version-one checks. No mutation. |
| `simulation/inventory_config.py` / `validate_inventory_scenario()` | Format dispatcher; scenario → None | Checks finite-positive integer/null capacity, unique IDs, compatible routes, nonoverlapping calendars, payload/speed, references, fixed trip scope, horizon and unsupported features. No mutation. |
| `simulation/models.py` / `record_event()` | Both engine paths and transition functions; either state, kind and details → None | Appends sequence/time/type/details. Only accepted state type widened; sequencing unchanged. |
| `simulation/models.py` / `build_inventory_state()` | New engine; scenario → InventoryRunState | Creates fresh products, vehicles, empty inventory/holds/AV queue and idle stations. No files or input mutation. |
| `simulation/runner.py` / `run_once()` | CLI/tests; input path and new output path → summary | Selects version-specific engine/summary; saves six artifacts and matching format versions. Retains byte-copy/hash/overwrite protection and completed-last manifest. |
| `simulation/runner.py` / `legacy_summary()` | Runner on version 1; legacy state → summary | Extracted unchanged old count/distance/wait calculation; no mutation. |
| `simulation/inventory_engine.py` / `run_inventory_simulation()` | Runner for version 2; scenario → settled InventoryRunState | Owns clock/heap, batches releases/completions/arrivals, queues plant arrivals, dispatches fixed trips and schedules returned completion times. Finalizes horizon travel/inventory/onboard waiting and logs horizon. No files. |
| `simulation/inventory_engine.py` / `settle_inventory()` | Engine after physical events; scenario/state → new completion-event tuples | Repeatedly retries AV unloading, starts one eligible station, then retries again before another start. Stops when no action changes state. |
| `simulation/inventory_engine.py` / `check_inventory_state()` | Engine after each settled batch; scenario/state → None | Asserts capacity, exclusive physical product conservation and payload; no mutation. |
| `simulation/inventory_engine.py` / `inventory_summary()` | Runner; settled state → summary | Derives terminal/generated/unfinished counts, actual vehicle distance, physical inventory wait and onboard wait. No mutation. |
| `simulation/routing.py` / `dispatch_inventory_trip()` | Engine at departure; scenario/state/trip → None | Reserves released requests by release-time/ID within payload, records actual quality-unaware policy input and assignment events. Does not move products. |
| `simulation/transport.py` / `depart_inventory_leg()` | Engine after dispatch or collection; scenario/state/AV/origin/target → arrival time | Calculates geometry/time, sets active leg/status and logs movement with copied load. |
| `simulation/transport.py` / `arrive_inventory_leg()` | Engine at due arrival; state/AV ID → None | Requires exact due time, adds distance, moves AV to target, clears leg and logs arrival. |
| `simulation/transport.py` / `collect_inventory_products()` | Engine at collection; scenario/state/AV ID → None | Moves this AV's reserved physically present products onboard in ID order; records pickups. Assignment already enforces payload and release eligibility. |
| `simulation/transport.py` / `retry_inventory_admissions()` | Settler; scenario/state → changed boolean | Sorts AVs by original arrival/ID, admits products individually while free space exists, records retry/admission or first wait, retains remaining onboard load and closes wait accounting when empty. |
| `simulation/plant.py` / `inventory_occupancy()` | Admission/assertion checks; inventory state → integer | No mutation; returns physical stock count plus held count. |
| `simulation/plant.py` / `fitting_window()` | Start function; station/time → merged fitting window or None | Merges adjacent intervals locally; requires full duration within half-open window with finish at close allowed. No mutation. |
| `simulation/plant.py` / `start_inventory_operation()` | Settler; scenario/state/station → end time or None | Selects eligible next-operation product by latest entry/ID, removes stock, accrues wait, occupies station and logs start. Adds intermediate hold or logs final-slot release for finite capacity. |
| `simulation/plant.py` / `finish_inventory_operation()` | Engine at completion; scenario/state/station ID → None | Clears station, records history, increments index once; either replaces hold with immediate stock and logs return or records terminal completion. |

`InventoryRunState` is a new dataclass: its generated constructor initializes
per-run maps/lists and time. It adds no handwritten methods and changes no files.
The existing `SimulationState` fields are unchanged. The existing `write_json()`
serializer remains unchanged and is explained in the checkpoint-one review.

### Concrete time-eight calls

Before 8: inventory `{'P1': 6}`, holds `[]`, AV2 load `['P2']`, original arrival
5 and waiting-since 5. P1 next index is 1 and WS3 is idle.

1. `settle_inventory(scenario, state)` first calls
   `retry_inventory_admissions(scenario, state)`. Occupancy is 1, capacity 1;
   it returns False and retains AV2/P2 unchanged. The wait was already logged.
2. `start_inventory_operation(scenario, state, WS3)` chooses P1, adds `8-6=2`
   wait, removes P1 from stock, sets WS3 active until 11, and returns 11. It logs
   sequence 21 then sequence 22, `inventory_slot_release`. Occupancy is now 0.
3. The settler loops back before considering another start.
   `retry_inventory_admissions()` sorts waiting AVs; AV2 is first. It logs
   `unload_retry` sequence 23 with arrival 5, moves P2 from AV2 into inventory,
   logs admission sequence 24 with counts waiting=1/held=0, and adds `8-5=3`
   onboard wait. AV2 becomes idle and leaves the waiting queue. It returns True.
4. P2 cannot fit WS1 until 9, so settling stops. The engine registers P1's
   completion at 11. There is no departure, travel or unloading delay at 8.

At time 6, `finish_inventory_operation(..., 'WS1')` instead removes P1 from
`held_slots` and inserts P1 into inventory at the same timestamp. Used capacity
stays 1, so that replacement does not permit AV2 to unload.

## Function review — independent verifier

All rows are in `verify_simulation.py`. Existing standard-library parser,
comparison, reporting and CLI helpers remain unchanged and retain their
checkpoint-two contracts. The new audit never imports simulation modules.

| Function/method | Caller; receives → returns | State/files and checks |
| --- | --- | --- |
| `validate_supported_scenario()` | Coordinator; scenario → None | Routes format 2 to independent inventory validator, preserving legacy validation. No mutation. |
| `validate_inventory_inputs()` | Version dispatcher; scenario → None | Own checks for capacity, scope, identifiers/references, calendars, supported routes, numerical bounds and trips; no simulator parser or mutation. |
| `verify_run()` | Existing verifier CLI; run path → report | Accepts matching versions 1/2, retains exact-byte fingerprint, dispatches to independent audit, sets coverage and exit precedence, writes the same two reports. |
| `InventoryAudit.__init__()` | Coordinator; scenario/findings → None | Builds independent empty state/input lookups, due-trip tracking, leg counts, follow-up list and expected decision list on the audit object. |
| `InventoryAudit.require()` | Audit methods; rule/expected/observed/event → None | Calls unchanged comparator; appends violation and raises ReplayViolation on mismatch. No physical mutation. |
| `InventoryAudit.match_fields()` | Event handler; event/expected payload → None | Checks every required payload value, rejects missing/extra fields; only findings change. |
| `InventoryAudit.occupancy()` | Capacity/deadline checks; self → integer | Counts own physical stock and held slots; no mutation. |
| `InventoryAudit.window_opportunity()` | Deadline/start checks; station/earliest time → start/window or None | Independently coalesces continuous intervals and computes full fit; no mutation. |
| `InventoryAudit.eligible_products()` | Deadline/start checks; station ID → ordered product IDs | Uses own stock, latest entries, true next operations and ID tie; no mutation. |
| `InventoryAudit.waiting_order()` | Deadline/admission checks; self → ordered AV IDs | Sorts original plant arrival then ID; no mutation. |
| `InventoryAudit.pending_deadlines()` | Clock/horizon checks; self → times list | Computes due releases/trips/arrivals/completions, possible starts and immediate free-space admission; no mutation. |
| `InventoryAudit.check_clock_boundary()` | Replay before advancing time; next event → None | Rejects unfinished mandatory follow-ups and skipped enabled actions; calls settled physical-state checker. Only findings change. |
| `InventoryAudit.check_physical_state()` | Clock/horizon checks; context event → None | Checks one physical location per released product, intermediate hold ownership and logged waiting state. Only findings change. |
| `InventoryAudit.capture_dispatch()` | Handler before first assignment/departure; event/AV ID → None | Independently selects released unassigned requests within payload, checks departure/AV-ID tie, records expected boundary decision and mandatory assignments/departure. |
| `InventoryAudit.consume_event()` | Replay; one observed event → None | Validates relevant IDs, precedence, required follow-ups, geometry, ownership, capacity, priorities, full windows and durations, then advances only its own physical state. Queues mandatory hold/return/release/retry-admission expectations. |
| `InventoryAudit.replay()` | Coordinator; observed events → reconstructed final state | Checks chronological unique sequences and deadlines, consumes events, requires settled horizon, finalizes partial travel and waits. No files. |
| `InventoryAudit.reconcile()` | Coordinator after replay; saved evidence → calculated summary | Independently derives counts/travel/inventory/onboard wait, attaches summary to own state, and compares complete final state, summary and actual dispatch records. Only findings/state change. |

At 8, `consume_event()` checks sequence 21 as P1's final operation and removes
its physical inventory membership with no new hold. It requires the very next
record to be P1's slot release, so an admission cannot precede that record.
Sequence 23 must retry the earliest waiting AV. Sequence 24 checks capacity
**before** accepting P2: own occupancy 0 + 1 <= capacity 1. It then checks load
ownership and recorded counts, transfers P2 and closes AV2's wait. The saved
counts do not establish capacity; the reconstructed state does.

In the mutated log at time 5, `consume_event()` instead has physical stock 0 and
P1's hold 1. The attempted admission means 0+1+1=2. `require()` records
`inventory.capacity` with event sequence 19 and actual IDs and stops unsafe
replay. The byte hash still passes because only the event log was edited.

The unchanged helpers used by this path are `main()` (parse CLI/return exit),
`read_evidence()` (read six files), `parse_json_evidence()` and
`reject_nonfinite_json()` (strict JSON parsing), `check_value()` /
`values_match()` / `add_finding()` (tolerant comparison and diagnostics), and
`write_reports()` (write only two verification reports). Their complete
contracts are in `checkpoint_2_review.md` and their implementations are untouched.

## Every new test/helper

All methods are in `tests/test_inventory.py`. unittest calls `setUp()` before
each test; each test receives only self and returns None or raises on failure.
Every scenario/log change is private temporary data and is cleaned after the
test. Tests call the independent verifier through a subprocess.

| Method | Caller, received/returned values and changes/checks |
| --- | --- |
| `setUp()` | unittest; self → None. Creates scratch directory, registers cleanup, loads fresh scenario and resets case counter. |
| `run_case()` | Tests; optional scenario → output path/events/final state. Writes temporary input and invokes runner to save one run; increments case counter. |
| `verify_case()` | Tests; run path/expected exit → report. Runs standalone CLI, asserts process/report exit and reads JSON report. Only verifier reports are written. |
| `save_events()` | Mutation tests; folder/edited records → None. Renumbers sequences and replaces only temporary events.jsonl using explicit UTF-8/LF. |
| `test_requested_slot_timeline()` | Checks all requested times/sequence numbers, exact four-record time-eight order, terminal state and independently reconciled totals. |
| `test_early_admission_exceeds_capacity()` | Moves P2 admission to 5, removes old admission/retry, renumbers, expects capacity failure at sequence 19 with attempted occupancy 2. |
| `test_waiting_vehicle_arrival_then_id_priority()` | Builds three-load cases testing earlier-arrival priority despite ID order and equal-arrival ID ties. Then corrupts retry AV and requires independent priority failure. |
| `test_workstation_inventory_fifo_and_id_tie()` | Admits three products together and checks WS1 P1→P2→P3 selection; then substitutes P3 for P2 and requires product-priority violation. |
| `test_partial_payload_stays_on_vehicle()` | Runs one capacity-two AV: P1 unloads at 4, P2 stays onboard until 8, onboard wait 4; verifies the run. |
| `test_horizon_retains_holds_and_onboard_stock()` | Runs horizons 0/1/4/5/6/7/8/9/11; checks P1's hold/P2 onboard at 5, immediate stock replacement at 6 and P2 admission at 8; verifies each snapshot. |
| `test_unlimited_capacity_and_adjacent_windows()` | Checks unlimited format-two capacity has no hold/block records and merges [4,5),[5,6) so a duration-two operation can finish at 6. |
| `test_missing_hold_or_falsified_replacement_fails()` | Removes hold evidence or falsifies hold replacement; both copied runs must fail. |
| `test_missing_retry_and_skipped_start_fail()` | Removes retry or all enabled time-eight actions; requires evidence/order/deadline failure. |
| `test_invalid_capacity_and_later_features_rejected()` | Rejects capacity 0, -1, 1.5, True and finite range. Changes only local scenario copies. |
| `test_exports_and_missing_evidence()` | Forges final held-slot state and expects FAIL; restores it, removes dispatch evidence and expects INCOMPLETE. |
| `test_saved_fixture_hash_lf_and_standalone_verification()` | Copies frozen six-file fixture, checks LF and SHA-256, runs verifier alone using isolated Python, checks all input bytes unchanged; appends a scenario newline and requires hash failure. |
| `test_zero_distance_and_empty_fleet()` | Verifies zero-distance immediate legs and no-fleet collection accumulation; checks distance 0 and appropriate completion counts. |

Tests invoke existing `run_once()` as setup for fresh runs. Its entire call path
is covered above; version-one functions and unchanged test functions retain the
contracts in earlier reviews. No existing tests were edited.

## Changed files

- `.gitattributes` (new hashed fixture LF rule), `README.md`.
- `configs/checkpoint_3a.json`.
- `simulation/__main__.py`, `config.py`, `models.py`, `plant.py`, `transport.py`,
  `routing.py`, `runner.py`.
- New `simulation/inventory_config.py`, `simulation/inventory_engine.py`.
- `verify_simulation.py`.
- New `tests/test_inventory.py`.
- Six new evidence files under `tests/fixtures/checkpoint_3a/`: `scenario.json`,
  `run_manifest.json`, `events.jsonl`, `dispatch_decisions.jsonl`,
  `summary.json`, `final_state.json`.
- New `docs/checkpoint_3a_review.md`.

No generated results, virtual environment or scratch files are source changes.
