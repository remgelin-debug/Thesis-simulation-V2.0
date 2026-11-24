# Checkpoint 4b — dynamic quality-unaware insertion

Created `thesis-checkpoint-4b` from checkpoint 4a commit
`dadfcb4f271a18ceacb11ff31216b46fde124126`. Implemented only 4b. Source, tests and documentation are maintained on `thesis-checkpoint-4b`.

## Inputs, policy boundary and search

`configs/checkpoint_4b.json` is a resolved version-3 multi-collection network.
It uses `collection_points` and no static trips. Products retain their actual
operation lists for plant processing. Dynamic runtime route stops explicitly
name `site_id`, `pickups` and `deliveries`; onboard parts retain committed
vehicle/destination. Adjacent same-site service stops are coalesced. There is
one remanufacturing plant, any configured recyclers and collection points.

The runner also accepts the existing version-2 single-point resolved/seeded
inputs with `--policy dynamic_quality_unaware`: only the execution copy becomes
version 3 and its fixed trips are ignored. Saved scenario bytes/hash remain
unchanged; the manifest records input version 2/output version 3 and selected
policy. Multi-point stochastic generation is not added. Legacy/static policy
paths and all 74 tests remain supported without changes to old test files.

At new-return releases and vehicle availability, notifications at the same time
are batched. Outstanding requests are considered by release time then product
ID. Each released request considers every idle/travelling AV, compatible
fixed-disposition destination, pickup position and subsequent delivery position.
A vehicle waiting to unload is excluded and retains its commitments. All
existing pickups/drop-offs, onboard products and destinations are preserved.

The explicit heuristic search domain includes zero, one or two extra facility
reset visits for finite AVs, at every position and configured facility. It does
not claim global route optimization or search beyond two extra visits per
insertion. Complete candidate routes are normalized/deduplicated by route,
vehicle and destination, retaining the first enumeration witness. Every retained
candidate, including infeasible ones, is recorded with feasibility/reason,
old/new stops, position/range and distance/score. The verifier independently
reconstructs this declared search domain rather than trusting candidate coverage.

Feasibility walks the whole remaining route from the actual position and
allowance: pickup ownership/readiness, pickup-before-delivery, payload after
each action, committed destinations and disposition compatibility, all travel
before each facility reset, and final reachable facility. New request data is
already released at the boundary; no future requests enter enumeration.

Primary ranking is added remaining distance compared with the current route
from the same position. The score is `[round(added_distance,12), vehicle_id,
destination_id, pickup_index, delivery_index, extra_visits, canonical_route]`.
The final fields provide stable ties; indexes refer to the pre-coalescing
insertion positions. Rounding at 12 decimals prevents negligible numerical
noise from defeating deterministic ties. It is a transport-distance heuristic,
not a load-balancing or delivery-urgency objective; an equal-distance insertion
may retain a later existing delivery rather than serve it at an earlier reset
visit. Products still keep their committed destination.

The ACTUAL selector input is recorded: released transport request ID, origin,
release time, disposition, state, ownership/destination; site IDs/coordinates/
roles; AV speed/payload/range, actual position/load and remaining plan. No
individual quality class, operations, workstation calendar or processing state
crosses this policy boundary. The selector receives only this record.

## Clock, cancellation and shared physical rules

Travel uses the same incremental accounting helper as static finite-range
travel. It also tracks unlimited dynamic AV positions. Each active leg stores
a unique ID and accounted prefix; past travel remains in total distance when
cancelled. A new leg starts at the actual current coordinates, not the old site.
A route change keeps the active leg if its target remains the same; otherwise
it logs `leg_cancelled`, invalidates the active leg and creates a fresh leg ID.
Pending arrivals contain that ID. A mismatched old arrival is discarded before
advancing the clock, so it cannot arrive, deliver, reset range or trigger routing.

Dynamic orchestration shares plant completion/start, inventory/held-slot,
admission-priority and recycler-acceptance helpers. Admission/acceptance helpers
now unload only parts committed to the current facility, preserving mixed loads
for later destinations. Waiting AVs remain blocked until their plant unload is
complete. After a facility stop finishes, a new availability notification is
batched; zero-distance continuations settle before dispatch, avoiding unchanged
same-time replanning loops. Range resets still occur on actual facility arrival,
before service or waiting.

## Expected and observed hand case

RF1 `(0,0)`, CP1 `(4,0)`, CP2 `(2,1)`, speed 1, payload 2, maximum 10. P1
releases at 0 at CP1; P2 releases at 5 at CP2. Both require one duration-1
operation at WS1, open `[0,12)`, unlimited plant capacity, horizon 12.

| Expected time | Actual time | Sequence | Actual event |
| --- | --- | --- | --- |
| 0 | 0 | 1–6 | Release P1, initial availability, dispatch/assignment/route, depart for CP1 |
| 4 | 4 | 7–10 | Arrive CP1 with 6 range; pick P1; depart toward RF1, due 8 |
| 5 | 5 | 11–12 | Release P2; dispatch observes position (3,0), load [P1], range 5 |
| 5 | 5 | 13 | Assign P2 to AV1/RF1 |
| 5 | 5 | 14 | Cancel AV1:L2, old due time 8, travelled prefix 1 |
| 5 | 5 | 15 | Revise remaining route: CP2 pickup, RF1 deliver P1/P2 |
| 5 | 5 | 16 | Depart actual position (3,0) as AV1:L3 |
| 5+sqrt(2) | 6.414213562373095 | 17–20 | Arrive CP2 with 3.585786437626905; pick P2; depart RF1 |
| 8 | no event | — | Old AV1:L2 arrival discarded; no arrival/delivery/reset |
| 5+sqrt(2)+sqrt(5) | 8.650281539872886 | 21–24 | Actual RF1 arrival, reset, both admissions |

At RF1, range is `1.3497184601271153`, then resets to 10. Total distance is
`4 + 1 + sqrt(2) + sqrt(5) = 8.650281539872886`.
Final counts: generated 2, remanufactured 2, unfinished 0, recycled 0. Plant wait
is 1 because one WS1 processes P1 then P2. Waiting to unload is 0.

At time 5, the old remaining distance is 3. The selected new distance is
`sqrt(2)+sqrt(5)=3.6502815398728847`, so added distance is approximately
`0.6502815398728847`. It fits the remaining allowance 5. Payload is 1 before
CP2, 2 after pickup, and 0 after RF1. P1's destination remains RF1.

Valid run: expected PASS/0, observed PASS/0. In a copied event log, changing
sequence 15 position to `[4,0]` gives expected FAIL/1 and observed:

```text
rule: dynamic.replan_position
time: 5
event_sequence: 15
vehicle: AV1, product: P2
expected: [3.0, 0.0]
observed: [4, 0]
```

The independent checker derives the expected position from the original return
leg and elapsed distance, not the decision's claimed position. It also checks
recorded boundary positions/ranges, every candidate and selected route. A
resurrected time-8 arrival with the cancelled leg ID is separately rejected.

All existing 74 and 16 new tests pass: 90 total. No old tests were changed.
The new six-file test fixture has an LF-pinned scenario; exact-byte SHA-256 and
independent isolated verification are preserved.

## Exact PowerShell commands

Run from the repository root with these changes present. The Python commands
and equivalent mutations ran in the cloud; PowerShell itself is unavailable
there. Syntax targets Windows PowerShell 5.1/7.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
# All 90 tests (includes the existing 74), then the 16 new tests alone:
& $thesisPython -m unittest discover -s tests -v
& $thesisPython -m unittest discover -s tests -p test_dynamic.py -v

$demoRoot = Join-Path .\results ("checkpoint-4b-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$valid = Join-Path $demoRoot valid
$invalid = Join-Path $demoRoot forged-position
& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_4b.json --policy dynamic_quality_unaware --output $valid
& $thesisPython .\verify_simulation.py $valid
"Valid verifier exit: $LASTEXITCODE"           # 0

$decisions = @(Get-Content (Join-Path $valid dispatch_decisions.jsonl) | ForEach-Object { $_ | ConvertFrom-Json })
$replan = $decisions | Where-Object { $_.time -eq 5 } | Select-Object -First 1
$replan.trigger | Format-List
$replan.policy_input.vehicles[0] | Format-List position,remaining_range,load,active_leg,remaining_stops
$replan.proposals | Format-List
$events = @(Get-Content (Join-Path $valid events.jsonl) | ForEach-Object { $_ | ConvertFrom-Json })
"Events at superseded time 8: $(@($events | Where-Object { $_.time -eq 8 }).Count)" # 0
Get-Content (Join-Path $valid summary.json)
Get-Content (Join-Path $valid final_state.json)

Copy-Item $valid $invalid -Recurse
$eventPath = Join-Path $invalid events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$revision = $events | Where-Object { $_.event_type -eq 'route_revised' -and $_.time -eq 5 } | Select-Object -First 1
$revision.position = @(4, 0)
$lines = @($events | ForEach-Object { $_ | ConvertTo-Json -Depth 80 -Compress })
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $invalid
"Forged-position verifier exit: $LASTEXITCODE" # 1
Get-Content (Join-Path $invalid verification.txt)
git check-attr text eol -- tests/fixtures/checkpoint_4b/scenario.json
```

Each simulation output directory must be new. No dependencies were added.

## Time-five call path

1. `run_dynamic_simulation()` selects the next real timestamp 5.
   `advance_dynamic_motion()` calls shared `advance_vehicle_travel()` BEFORE
   applying P2 release. For old AV1:L2, elapsed time is `5−4=1`, so its prefix
   advances from 0 to 1. Actual position is `(4,0)+(1/4)*((-4),0)=(3,0)`;
   distance grows from 4 to 5, allowance falls from 6 to 5.
2. Release P2 and all other due physical changes settle. The engine batches
   trigger `released_products=[P2], available_vehicles=[]` and calls
   `dispatch_dynamic_requests()`, which records physical dispatch marker 12.
3. `build_dynamic_policy_input()` records the actual motion, load P1, destination
   commitments and remaining RF1 stop. `choose_dynamic_insertions()` calls
   `enumerate_dynamic_candidates()` for outstanding P2.
4. Each route is normalized by `merge_route_stops()`, checked fully by
   `check_insertion_route()`, measured by `remaining_route_distance()` and scored.
   The winner puts CP2 before the old RF1 stop and coalesces both RF1 deliveries.
   The direct tail length 3.65028154 is feasible with allowance 5 and payload 2.
5. Dispatcher records the complete candidates/selection, reserves P2, logs
   cancellation of old L2 and its travelled prefix, clears that active leg,
   logs revision 15 and updates the remaining service plan. P1 remains onboard.
6. `start_dynamic_leg()` allocates new identity L3 from actual `(3,0)` to CP2,
   leaving distance already travelled 5 intact. Arrival is scheduled for
   `5+sqrt(2)`. L2's old heap entry remains tagged L2 but is no longer live.
7. At CP2 the engine records arrival/pickup/stop completion, starts L4 to RF1.
   When the heap reaches the stale entry due at 8, its ID differs from active
   L4, so it is discarded BEFORE a clock advance or service callback.
8. At real RF1 arrival 8.65028154, shared reset/admission/start helpers run.
   Independent replay performs the same geometric reconstruction with its own
   implementation and rejects the copied false position before applying it.

## Every new or changed simulation function

All have descriptive names, type hints and short docstrings. `Record` denotes
JSON-shaped records. No runtime helper writes files directly; the runner saves
six artifacts after one run. Policy functions receive only their recorded input.

| File / function | Caller; receives → returns | State/files and rules |
| --- | --- | --- |
| `simulation/__main__.py` / `main()` | Python entry; CLI → exit integer | Accepts dynamic_quality_unaware alongside both static policies. Existing source/seed guards remain. |
| `simulation/config.py` / `validate_scenario()` | Loader; scenario → None | Routes version 3 to dynamic validator, retaining versions 1/2. No mutation. |
| `simulation/runner.py` / `run_once()` | CLI/tests; source/output paths and optional seed/policy → summary | Selects dynamic execution/format conversion, preserves saved bytes/hash, records input/output versions and writes existing six artifacts. Version-3 static overrides are rejected. |
| `simulation/dynamic_config.py` / `as_dynamic_scenario()` | Runner/tests; source scenario → copied dynamic record | Converts single collection point to list, sets dynamic execution policy/version and clears fixed trips; never changes source bytes/record. |
| `simulation/dynamic_config.py` / `validate_dynamic_scenario()` | Loader; dynamic scenario → None | Checks multi-point IDs/coordinates/origins/no fixed trips; validates common physical rules through an isolated inventory-schema proxy. No input mutation. |
| `simulation/dynamic_routing.py` / `merge_route_stops()` | Enumerator; stops → copied normalized stops | Coalesces adjacent same-site services and sorts/deduplicates IDs. No input mutation. |
| `remaining_route_distance()` | Enumerator; actual position/stops/site lookup → distance | Sums complete tail geometry; no mutation. |
| `check_insertion_route()` | Enumerator; vehicle/complete route/transport requests/sites/new ID/destination → feasible boolean/reason | Walks all range/payload/ownership/precedence/commitments and checks facility escape; changes only local sets/allowance. |
| `enumerate_dynamic_candidates()` | Selector; policy input/new request → candidate list | Enumerates positions/destinations/reset visits, deduplicates and records every retained candidate, feasibility, distances and stable score. No live changes. |
| `choose_dynamic_insertions()` | Dispatcher; recorded input → proposals/request audits | Copies input, processes release/ID order, chooses minimum feasible score and updates only the local working commitments for subsequent requests. |
| `build_dynamic_policy_input()` | Dispatcher; scenario/state → transport-only snapshot | Exposes released transport data/sites/motion/commitments; strips all quality/operations and plant-processing information. No mutation. |
| `simulation/dynamic_engine.py` / `build_dynamic_state()` | Engine; dynamic scenario → new InventoryRunState | Adds tracked positions, finite/null range, route list, revision and leg counters for each AV. |
| `advance_dynamic_motion()` | Engine before real timestamps/horizon; scenario/state/time → None | Delegates to the shared actual-travel helper for finite and unlimited positions. |
| `start_dynamic_leg()` | Engine after settling/dispatch; scenario/state/AV ID → tagged arrival tuple or None | Guards active/waiting/empty plans and finite leg budget; allocates leg ID, records actual origin coordinates, target/times/load and starts travel. |
| `service_dynamic_stop()` | Engine on live arrival; scenario/state/AV ID → None | Picks only owned released stop IDs; queues plant service or invokes shared recycler acceptance for committed loads. |
| `finish_dynamic_stops()` | Engine after plant settling; scenario/state → newly available AV IDs | Pops fully served arrived stops, logs stop completion and facility availability; retains other onboard commitments. |
| `dispatch_dynamic_requests()` | Engine on batched triggers; scenario/state/trigger → None | Records boundary/decision, applies assignments, cancels changed targets, records old/new routes and revision position/range atomically. Does not count cancelled travel again. |
| `run_dynamic_simulation()` | Runner; dynamic scenario → settled final state | Owns event batching, live-leg token checks, physical settling, trigger batching and horizon finalization. Stale arrivals are discarded before clock movement. |
| `simulation/vehicle_range.py` / `advance_vehicle_travel()` | Static/dynamic clock helpers; scenario/state/time → None | Shared prefix distance/range/position integration now also tracks unlimited AVs with position records; old untracked unlimited static accounting remains unchanged. |
| `simulation/transport.py` / `retry_inventory_admissions()` | Shared settler; scenario/state → changed flag | Admits only the load committed to this plant, one-at-a-time by arrival/AV priority; other destinations remain onboard. Existing static behavior preserved. |
| `simulation/transport.py` / `accept_recycling_products()` | Static/dynamic recycler arrival; scenario/state/AV ID → None | Accepts only products committed to the current recycler; other destinations stay onboard. Eligibility/terminal updates unchanged. |

The unchanged plant start/completion, finite holds, workload selection, range
reset and stock-conservation helpers retain their prior contracts and are used
by both orchestration paths. There is no dynamic quality-aware forecast/ranking.

## Every new or changed independent-verifier function

All are in `verify_simulation.py`. No simulator/routing/engine code is imported.
The independent dynamic class inherits only this verifier's plant/check helpers.

| Function/method | Caller; receives → returns | State/files and checks |
| --- | --- | --- |
| `validate_supported_scenario()` | Coordinator; scenario → None | Adds dynamic version-3 validator dispatch; legacy checks unchanged. |
| `audit_dynamic_shape()` | Coordinator; original inputs → copied dynamic shape | Converts a version-two execution override independently; does not touch saved bytes. |
| `validate_dynamic_inputs()` | Format dispatcher; scenario → None | Independently checks multi-point IDs/origins/coordinates and common physical limits via its own inventory validator/proxy. |
| `audit_merge_stops()` | Audit enumerator; stops → canonical copied stops | Independently coalesces service lists. No replay mutation. |
| `audit_route_distance()` | Audit enumerator; position/stops/sites → distance | Independently sums coordinates with math.hypot. No mutation. |
| `audit_dynamic_feasibility()` | Audit enumerator; vehicle/route/requests/sites/new request/destination → boolean/reason | Independently reconstructs projected load, precedence, destinations, resets and full-route range. |
| `audit_dynamic_candidates()` | Audit selector; boundary/request → complete expected candidates | Own declared-domain enumeration/deduplication, feasibility/distances/scores and stable IDs. |
| `audit_dynamic_selection()` | Audit dispatch; expected boundary → proposals/audits | Own release/ID iteration, candidate minimum and working-commitment updates. No live replay mutation. |
| `DynamicAudit.__init__()` | Coordinator; normalized inputs/findings → object | Initializes independent physical state, all sites, actual motion/routes/counters, trigger notices and expected proposals. |
| `DynamicAudit.policy_input()` | Dispatch audit; self → expected transport-only boundary | Reconstructs from own state and permitted fields, never trusts observed input or adds quality. |
| `DynamicAudit.pending_deadlines()` | Inherited clock/horizon checks; self → due times | Adds pending dispatch, service and continuation deadlines so actions cannot be silently skipped. |
| `DynamicAudit.advance_range_travel()` | Replay before each event; time/event → None | Own finite/unlimited motion integration, preserving cancelled prefixes and checking allowance. |
| `DynamicAudit.prepare_dispatch()` | Handler on dispatch marker; event → None | Checks actual batched releases/availability and settled arrivals/services, independently rebuilds candidates and queues atomic assignment/cancel/revision/departure expectations. |
| `DynamicAudit.consume_event()` | Replay; observed event → None | Checks dynamic leg identity, actual revision position/range, load/commitment services and valid availability. Delegates unchanged processing/hold/reset/horizon rules to independent base checker. |
| `DynamicAudit.replay()` | Coordinator; events → reconstructed horizon state | Chronological replay and deadline checks with actual motion; finalizes plant/onboard wait without duplicate horizon travel. |
| `DynamicAudit.reconcile()` | Coordinator; saved evidence → independently calculated summary | Checks decision count, position/range/trigger/input/candidates/proposals, then uses existing independent complete export reconciliation. Only findings/own state change. |
| `verify_run()` | Standalone CLI; folder → report | Recognizes input/output format combinations, dynamic execution policy and independent audit; retains original-byte hashes, seed audit, reports and exit precedence. Writes only verification.json/txt. |

At event 15/time 5, the checker has integrated one unit along L2 from `(4,0)`
toward `(0,0)`. Its state is `[3,0]`/range 5, even if the copied revision claims
`[4,0]`. `dynamic.replan_position` fails before route state changes. At the
real return arrival it requires the current L4 identity, so an inserted L2
arrival at 8 cannot be treated as delivery or reset.

## Every new test/helper

All methods are in `tests/test_dynamic.py`. unittest calls `setUp()` before
each test; tests receive self and return None or raise assertions. All run/log
mutations are private temporary files and cleaned; saved fixture is read-only.

| Method | Caller, inputs/returns, state/files and checks |
| --- | --- |
| `setUp()` | unittest; self → None. Creates scratch root/cleanup, scenario copy and counter. |
| `run_case()` | Tests; optional scenario → folder/events/decisions/final state. Writes temporary input and invokes one run. |
| `verify_case()` | Tests; folder/expected exit → report. Executes standalone checker, asserts status and reads report. |
| `save_records()` | Mutation tests; folder/filename/records → None. Renumbers event sequences when needed and writes only that UTF-8/LF JSONL copy. |
| `test_requested_mid_leg_reroute()` | Checks exact position/range/load, added distance, all expected milestones/total, silence at 8, cancelled prefix and quality-free inputs; verifies. |
| `test_forged_replan_event_position_fails()` | Forges revision position at 5 and checks precise rule/time/sequence/expected/observed diagnostic. |
| `test_forged_dispatch_position_or_candidates_fail()` | Forges boundary position or candidate added distance; requires independent audit failure. |
| `test_capacity_forces_delivery_before_second_pickup()` | Payload 1 blocks direct pickup; current RF arrival stays live and P2 follows unloading; verifies. |
| `test_full_route_range_can_force_a_later_insertion()` | Range 8 leaves 3 at time 5: pickup leg alone is reachable but direct complete detour is not. Requires RF reset before P2 pickup. |
| `test_infeasible_request_waits_and_is_reconsidered()` | Range 7 leaves unreachable P1 waiting/unassigned, serves P2 and checks repeated valid reconsideration triggers. |
| `test_unlimited_range_still_integrates_cancelled_motion()` | Checks same reroute distance without range resets or finite allowance. |
| `test_horizon_inside_replacement_leg_counts_only_actual_travel()` | Horizon 6: distance 6/range 4 and interpolated new-leg position, P1 onboard, P2 not yet picked; verifies. |
| `test_simultaneous_releases_are_batched_before_one_departure()` | Releases both at 0, requires one batched decision with two proposals and one actual initial leg. |
| `test_zero_distance_and_empty_fleet()` | Checks finite same-time zero-distance settling and stock accumulating without AVs. |
| `test_waiting_vehicle_keeps_commitment_until_capacity_frees()` | Full held-slot plant blocks AV, excludes new insertion at 5, admits committed P2 at 8 and reconsiders outstanding work then. |
| `test_mixed_dispositions_preserve_different_destinations()` | Carries recyclable/remanufacturable together and checks only correct committed parts unload at RF1/RC1. |
| `test_inserted_facility_visit_makes_range_feasible()` | Adds an explicit recycler reset visit while retaining a remanufacturable RF1 commitment; no invalid recycling occurs. |
| `test_resurrected_superseded_arrival_fails()` | Inserts cancelled old time-8 arrival, renumbers and requires live-leg audit failure. |
| `test_seeded_static_inputs_can_run_and_replay_dynamically()` | Uses existing single-point seeded config with dynamic override; checks identical replay artifacts, input 2/output 3 and both PASS. |
| `test_frozen_dynamic_fixture_lf_hash_and_standalone_verifier()` | Checks LF/hash, verifies using only an isolated copied standalone script, preserves all six evidence bytes. |

## Changed files

- `.gitattributes`, `README.md`.
- New `configs/checkpoint_4b.json`.
- `simulation/__main__.py`, `config.py`, `runner.py`, `transport.py`, `vehicle_range.py`.
- New `simulation/dynamic_config.py`, `dynamic_routing.py`, `dynamic_engine.py`.
- `verify_simulation.py`.
- New `tests/test_dynamic.py`.
- New six-file fixture `tests/fixtures/checkpoint_4b/`: scenario.json,
  run_manifest.json, events.jsonl, dispatch_decisions.jsonl, summary.json,
  final_state.json. Its scenario is pinned to LF.
- New `docs/checkpoint_4b_review.md`.

No old tests/fixtures changed. Generated results, scratch and environment files
remain ignored. Stopped after checkpoint 4b for review.
