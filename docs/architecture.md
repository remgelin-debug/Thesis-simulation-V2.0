# Simulation architecture

The refactor separates physical execution from tools while keeping all existing
policies and ordinary-run artifacts for formats two and three. Format one is
retired as described below. F4/F5 additionally enforce the
physical validity and representability contracts documented below. The normal entry point is
`python -m simulation`; no plotting package is imported for a headless run.
Significant functions document their callers, input/return contracts, mutations,
events and assumptions at their definitions. Simple helpers have short docstrings.

## Format-one retirement and retained evidence

The previous version is `simulation-v8-readable-code-layout` at
`1753c587da13442914743da841d867e712b6cdb1`. Historical execution requires that
version. The scalar `SimulationState`, its event loop, plant/transport helpers,
runner export branch and independent scalar audit are removed, with no shims.
A single product remains an ordinary one-item `products` list in format 2 or 3.

| Input | Supported execution |
| --- | --- |
| Format 2 | Static unaware/aware fixed inventory trips; dynamic unaware/aware via policy override |
| Format 3 | Dynamic unaware/aware network routes |
| Format 1 or unsupported/missing declaration | Simulation rejects before output creation; no automatic conversion |

Fixed/static routing is current functionality. Its departure schedule, product
selection, processing forecasts, fixed range plan and travel/pickup functions
still feed the shared inventory event loop. Primary plant fields still refer to
the authoritative plant's containers; they are not removed or counted twice.
Generation streams and version numbers are unchanged. Experiment manifests and
single-site generation provenance can still use their own version 1.

The verifier independently recognizes formats 2 and 3. Historical format-one
results are unsupported: `scenario.scope` explains retirement, reports are fresh,
`replay_complete` is false, and the result is INCOMPLETE/2 unless separate usable
evidence proves a violation. Raw-byte hash contradictions still take precedence
as FAIL/1. Missing/malformed format declarations remain evidence problems. Existing
format-two/three diagnostic values/order stay unchanged, including the historical
`Supported checkpoint-one scenario` expected-value label for retained invalid
scope reports. The no-usable-scenario scope header also keeps its historical
placeholder. These are compatibility labels, not active scalar support. Known
container-shape checks remain to preserve strict malformed-evidence diagnostics;
they do not authorize a format or calculate scalar transitions.

Replay checks supported format before creating display state. The viewer reports
that ValueError through its existing CLI error path. KPI export names/skips
retired runs, preserves supported rows in mixed folders, and keeps its existing
skip exit code; it does not trust an archived PASS to establish supported scope.
Experiment refresh writes INCOMPLETE for an unsupported member, retains the
attempt and removes the whole incomplete group from eligible paired results.
Neither verifier nor KPI imports simulation calculations; both remain standalone.

Earlier acceptance counts and benchmark tables below describe their named
historical revisions, some of which supported format one. They are not claims
that the retired format still executes. Current retirement acceptance and the exact test migration list are recorded
in [tests/README.md](../tests/README.md#retirement-acceptance-against-readable-layout). Historical fixture/input bytes and archived reviews remain intact.

## Responsibilities

| Location | Responsibility |
| --- | --- |
| `simulation/__main__.py` | Parse unchanged single-run flags; call runner; lazily open optional viewer. |
| `simulation/runner.py` | Prepare/load inputs, choose engine, create output folder and save artifacts. |
| `simulation/config.py` | Validate physical declarations and generation distributions. Adapt format-two singular sites for dynamic execution; reject retired/unsupported formats. |
| `simulation/state.py` | Typed records, fixed definition indexes, mutable state, physical snapshots, events, plant views and totals. |
| `simulation/generator.py` | Stream derivation, coordinates, arrival batches, qualities/operations, calendars and complete scenario preparation. |
| `simulation/engine.py` | Shared inventory clock/heap, fixed and dynamic decision application, settlement and horizon for all retained inventory/network runs. |
| `simulation/numerics.py` | Finite derived geometry, advancing travel/processing times and finite physical totals; pure checks, no rounding or model tolerances. |
| `simulation/transport.py` | Legs, collection, plant unloading, recycling eligibility, range feasibility/consumption/reset. |
| `simulation/plant.py` | Availability fitting, operation transitions, held capacity, shared settling and conservation. |
| `simulation/routing.py` | Permitted observations, fixed product selection, dynamic insertion alternatives, routing feasibility and all policy scores/ties. |
| `simulation/forecasting.py` | Copy released physical state and project admission/first start with revised commitments. |
| `tools/replay.py` | Read-only event-prefix snapshots, interpolation and playback controls; no plotting/engine imports. |
| `tools/viewer.py` | Optional Matplotlib map, plant details, controls, calendars and processing overlays. |
| `tools/verify.py` | Independent generation/physics/policy reconstruction and evidence reports; standard library only. |
| `tools/experiments.py` | Existing format-two paired replications, child runs/checks, manifests and CSV comparisons. |
| `tools/kpis.py` | Independent event-interval KPI integration and Excel-ready batch/run CSV; standard library only. |

The root verification, experiment and KPI scripts are small CLI compatibility
wrappers delegating to tools; there are no duplicate implementations. The viewer
command is `python -m tools.viewer RUN_FOLDER`.
Old internal Python module paths are intentionally consolidated. Old checkpoint
configurations are either existing audited fixture scenarios or `tests/inputs/`.
The one current user configuration is the unmodified three-site pilot.

## Module-path migration

The compatibility paths `simulation.fixed_engine` (`simulation/fixed_engine.py`)
and `simulation.visualization` (`simulation/visualization.py`) are retired.
Import `dispatch_inventory_trip` and `run_inventory_simulation` from `simulation.engine`;
launch saved-run visualization with `python -m tools.viewer RUN_FOLDER` or call
`tools.viewer.open_viewer`. No replacement shim or module alias is installed.
The optional simulation `--visualize` path already imports that same viewer lazily
and remains unchanged. Root verification, experiment and KPI wrappers are retained.
Historical checkpoint documents and earlier acceptance notes describe previous
revisions, including the aliases available then; they are not current entry-point
instructions.

**Historical wrapper-removal validation, before format-one retirement.** Against performance-memory commit
`523b180dc7510b28ac8af3fd16c2fed8f11f7855`: baseline and updated suites both pass
272 tests with Matplotlib installed. The 27-case matrix matches 135/135 physical
artifacts, all manifests (with each run ID checked against its folder), and
54/54 valid report files. Eight additional CLI runs match 40/40 physical files,
including legacy/static/dynamic execution, both pilot policies and optional
`--visualize`; saved-run and integrated PNGs match baseline. All runs verify.
KPI exports contain eight rows with zero skips and identical baseline/updated CSV
bytes. Standalone/root wrappers and execution with plotting imports blocked pass.
All replay/control/processing-overlay assertions remain. Graphical checks use
Agg; an interactive desktop window was not tested. A repeated tracked-source
search finds retired names only in migration notes and explicitly historical
documents, with no active Python imports or commands.

## Confirmed V8 simplification cleanup

The confirmed V8 cleanup retains the execution architecture. Its shared functions
name existing contracts; none adds a routing policy, changes evidence or performs
simulation work on import.

| Proposal | Current implementation and ownership |
| --- | --- |
| S04/S09 | Correct copy/FIFO/scope and historical descriptions; remove a discarded test context; keep the optional-import test meaningful under optimization. |
| S01 | `state.merge_adjacent_windows()` reads ordered bounds and returns owned immutable pairs. Preparation and direct fitting share only that exact-adjacency rule. |
| S02 | `generator._canonical_resolved_scenario()` returns the decoded canonical graph and its exact UTF-8/LF bytes after the existing validators run. |
| S06 | Independent forecast loops retain their own mechanics and earlier nonempty checks; only the redundant second eligibility conditions were removed. |
| S05 | Network audit proxies exclude only the replaced top-level product/batch lists before deep-copying retained declarations; diagnostic ownership is preserved. |
| S03 | Local/network capacity wrappers delegate membership/payload checking over repeatable borrowed plant-container tuples; they retain scope and error ordering. |
| S07 | Six test classes directly use one CLI/report-exit assertion function; production tools and stricter/different test helpers remain separate. |

S08 remains deferred: there is no viewer cache in this change. D1 failure-evidence
retention remains separate feature work. Original review reports are preserved
unchanged and are not included in the cleanup commits.

### Cleanup acceptance

Baseline: wrapper-removal `604442ebf938c7c37eea89538b188bce9abba11b`, fetched with
no subsequent commits. An untouched worktree passed 272 tests. The cleanup passes
288 tests under ordinary Python and `PYTHONOPTIMIZE=1` with Matplotlib installed.
All 272 original discovered test identities and their assertion sequences are
retained; 16 focused tests cover explicit calendar intervals/types/ownership,
canonical bytes/order/precision, verifier copy/diagnostic ownership, conservation
scope/first-error ordering and the shared test CLI contract. Direct invocation of
the six migrated test modules passes all 87 tests.

The established 27-case matrix passes independent verification in both worktrees:
135/135 scenario/event/decision/summary/final-state artifacts and 54/54 valid
JSON/text verification reports match byte-for-byte. All 27 manifests match every
field after checking each run ID against its own folder name. No physical values,
candidate order, JSON representation or unexpected difference was normalized.

Seventy copied invalid-evidence cases retain all 140 report-file bytes, findings,
order, contexts, expected/observed values, classifications and CLI exits: 16 FAIL
and 54 INCOMPLETE. They include coherent product/batch/quality/coordinate/calendar
and adjacent large-seed alterations, missing fields/files, malformed structures,
unsupported scope and combined proved violations with unusable evidence.
All are reverified for stable fresh reports; 36 provenance/prior-report combinations
cover absent/PASS/FAIL/INCOMPLETE history. Explicit report I/O failures remain
nonzero and do not claim fresh reports. Twenty-seven additional local/network/
fallback guard probes retain identical outcomes and exact diagnostics in normal,
`-O` and `PYTHONOPTIMIZE=1` execution.

Supplemental CLI checks retain 40/40 physical files, 16/16 valid reports and eight
manifests, including optional `--visualize`, the preferred viewer, standalone/root
verifier/KPI commands and blocked graphical imports. Twelve seed-42/43/54 paired
and reverse-order/repeat runs plus two eight-run experiments retain another
140/140 physical files, 56/56 reports and 28 manifests. Repetition/order checks
preserve identical resolved inputs and physical outputs. Experiment CSV exports
match, and refresh retains all eight attempts while excluding the affected
four-policy group: malformed dispatch gives eight KPI rows (seven PASS, one fresh
INCOMPLETE), zero skips; missing events gives seven rows and one named skip.
Valid seed/CLI KPI batches have zero skips and baseline-identical BOM CSV bytes.
Across those 63 valid comparison runs, manifests also match as raw bytes, with
each folder's run ID checked independently. Ninety-three selected replay
snapshots and six same-environment Agg RGBA renders match the baseline, including
long processing history and nine-row pilot presentation. Integrated/saved PNG
comparisons and existing callback/backward-step/overlay tests also pass.

No speedup or allocation percentage is claimed. The verifier avoids copying two
collections that it replaces, with that boundary and retained nested ownership
tested directly. S08 stays deferred and D1 is not implemented. Graphical checks
use Agg rendering and callbacks; an interactive Windows desktop was not tested.

## Normal call sequence and saved evidence

1. `__main__.main()` checks arguments and calls
   `runner.run_once(input_path, output_path, seed=42, policy=...)`.
2. For `--config`, `generator.prepare_scenario()` reads and validates settings.
   Network preparation calls the return/quality and schedule generators and
   optional coordinate sampler. Each uses its original private stream identity.
   It returns the resolved dictionary **and canonical LF bytes**, with all products,
   site coordinates, calendars and seed provenance. For `--resolved-scenario`,
   `config.load_scenario()` returns the parsed dictionary **and original bytes**;
   generation is skipped completely.
3. The runner preserves those input bytes unchanged. For dynamic policies,
   `config.as_dynamic_scenario()` makes an execution copy, converts older singular
   site fields and applies the policy override there. Hashing/saving does not
   depend on policy execution order. All retained execution paths use
   `InventoryRunState`, including single-product inventory examples.
4. The engine builds state and processes its event heap. Inventory entry points
   call `execute_inventory()` with an explicit fixed/dynamic distinction. Dynamic
   execution advances actual motion before handling a live timestamp. On release
   or vehicle availability, `routing.build_dynamic_policy_input()` makes a copied
   policy boundary and `choose_dynamic_insertions()` ranks feasible routes.
   `dispatch_dynamic_requests()` applies selected routes and assignments. Physical
   functions append events via `state.record_event()`; only the runner writes logs.
5. At each timestamp, shared `plant.settle_plant_network()` / `settle_inventory()`
   retries unloads and starts work until no immediate action remains. New completion
   tuples go back to the heap. Engines check conservation after settlement.
6. At the horizon, accrue partial travel, unfinished inventory waits and unload
   waits. Return final state. The runner writes `events.jsonl`,
   `dispatch_decisions.jsonl`, `summary.json` and `final_state.json`, then marks
   `run_manifest.json` completed. A pre-existing output directory is an error.
7. Tools read the saved evidence. Verification writes only `verification.json`
   and `verification.txt`; replay does not execute physics. KPI export writes its
   requested CSV. A failed simulation retains the resolved scenario and running
   manifest; event/decision logs are written only after successful execution.
   Retaining additional failure evidence remains the separate D1 feature.

## Consolidated execution

The runner imports two execution APIs from `engine.py`. Format-two static
policies call `run_inventory_simulation()`. Dynamic policies call
`run_dynamic_simulation()`, including supported format-two inputs adapted on a
separate execution copy. Both call `execute_inventory()` with the existing
fixed/dynamic distinction. `dispatch_inventory_trip()` applies scheduled static
choices; `dispatch_dynamic_requests()` applies dynamic choices. The scalar
`run_simulation()` and `dispatch_fixed_trip()` APIs were retired with format one;
there is no replacement wrapper or automatic format-one conversion.

Inventory events use five-tuples with time, priority, identifier, kind and token.
Fixed events append an empty token to their former four-tuple ordering. Fixed
window identifiers remain station IDs. Dynamic windows retain plant IDs, and
multi-plant completions retain plant/station tokens. The unified representation
therefore retains the ordering recorded in the table above.

At a live timestamp, the loop advances actual travel, sets the clock and drains
the pending batch. Both modes release products, complete work through a local
plant view and process arrivals through their mode-specific transport service.
Both then settle plants in sorted plant-ID order. Within each plant, unloading
is retried before each workstation start. A final-operation start frees capacity,
so waiting AVs get another admission attempt before another station starts.

Static execution then applies scheduled departure decisions and checks
conservation. New zero-distance arrivals are processed in the next batch at that
same timestamp. Dynamic execution finishes served stops and starts ready
committed legs. It drains any new same-time events before invoking routing for
accumulated releases and availability, then repeats if routing creates a
zero-distance departure. Dispatch-disabled projections follow that physical
sequence while suppressing the routing invocation.

Each inventory state owns `plant_states[plant_id]`, including a one-plant run.
Its top-level inventory, held slots, waiting vehicles and workstations reference
the primary plant's containers. Network local views share only their owner's
containers, fleet, products and event lists. Their clock is copied at creation,
so execution recreates views at each timestamp. Forecasts first copy the physical
snapshot, then bind aliases within that private state. A single-plant saved
snapshot omits the internal map to retain its existing schema. Loading such a
snapshot recreates the map from its top-level containers.

`check_inventory_state()` retains its local/manual-state scope;
`check_plant_network()` checks authoritative plants and falls back locally only
when the plant-ID map is absent. Each checks capacity first, then delegates to
the private plant membership/payload kernel with a repeatable tuple of borrowed
containers. The local wrapper constructs only a view of its existing references;
neither wrapper copies or initializes physical state. The kernel counts load
occurrences, ignores holds as additional products and never counts primary aliases
twice. Product-state order, vehicle-definition order, first errors and diagnostic
context are retained. The independent verifier keeps its own checks.

At the horizon, tracked vehicles accrue their unaccounted traveled prefixes.
Fixed unlimited vehicles retain arrival-based distance accounting and accrue a
remaining partial leg once. The shared horizon helper adds unfinished inventory
and unload waits, counts each authoritative plant once and leaves later jobs
active. No separate scalar horizon path remains.

The focused completion example starts from the slot fixture with WS1 duration
four and windows `[4, 8]`, `[8, 12]`. At time 8 the recorded sequence is
`operation_complete(P1)`, `inventory_return(P1)`, final
`operation_start(P1)`, `inventory_slot_release(P1)`, `unload_retry(AV2)`,
`plant_admission(P2)`, `operation_start(P2)` and
`inventory_slot_release(P2)`. The completion frees WS1 and replaces P1's held
slot with inventory. Starting P1's final operation frees inventory capacity,
allowing P2's admission and processing at the same timestamp.

For the mid-leg reroute, `AV1:L2` originally scheduled RF1 arrival at time 8.
At time 5 the engine accounts travel to `(3, 0)`, cancels that leg and starts
the CP2 diversion. `discard_stale_arrivals()` removes the obsolete heap entry
before any time-8 motion/clock advance. Token checks also ignore stale arrivals
inside a batch with other legitimate events. The actual facility arrival and
range reset occur at approximately 8.65028154.

## Routing and forecasting interfaces

| Policy | Invocation and permitted choice | Selection |
| --- | --- | --- |
| Static unaware | Scheduled format-two departure, configured stops/destination, released unassigned products | Destination-compatible products in release-time/product-ID order, up to payload capacity |
| Static aware | Same departure, stops and destination | Forecast each eligible product for each remaining payload slot, including products already selected for that trip |
| Dynamic unaware | Return releases or vehicle availability, actual fleet state and existing commitments | Feasible pickup/delivery insertions ranked by added distance and stable ties |
| Dynamic aware | Same triggers, insertion alternatives and feasibility | Predict each feasible insertion's admission and first processing start, then apply the existing aware score |

The engine's `dispatch_inventory_trip()` calls `build_static_policy_input(scenario, definitions, state, trip)`
to obtain ordered eligible requests and the fixed stops. It calls
`choose_static_products()` with that observation, vehicle capacity, destination
disposition, pickup-site ID, range feasibility and awareness. Selection returns
the existing `decision` and `tie_break` fields, plus aware `candidates` and
`selection_reason`. The engine adds timestamp/trigger/event-sequence fields,
appends the dispatch record, applies assignments and emits assignment events.
The shared transport range calculation supplies full-trip feasibility and the
engine records its check before the dispatch record, as in stage 2.

The dynamic engine calls `build_dynamic_policy_input()`, then
`choose_dynamic_insertions(policy_input)`. The selector owns a working copy of
the observation. It returns selected proposals and per-request candidate audits,
retaining rejected alternatives and their reasons. Each winner changes only the
working routes and request ownership used to evaluate subsequent requests. The
engine records the original observation and selection, applies assignments and
route replacements, cancels superseded arrivals and starts the revised legs.

Both unaware observations contain transport facts and the remanufacture/recycle
flag. Their execution paths skip processing context construction and projection.
Distance, ETA, payload and facility-to-facility range checks still run as required
for travel feasibility. Aware observations additionally contain operations and a
released-only physical context built by forecasting.

Routing calls `forecasting.forecast_candidate(context, definitions, trip,
product_id, selected)` for a fixed-trip prediction. For dynamic candidates it calls
`forecasting.forecast_dynamic_insertion(policy_input, candidate)`. Both return
arrival, admission, first-start and inventory-wait predictions. Dynamic results
also report revised committed-delivery ETAs. Forecasting owns each mutable
projection and reads fixed definitions. Routing owns the candidate records,
attaches the returned predictions, computes scores and selects winners.

Static `quality_candidate_score()` and dynamic `dynamic_quality_score()` remain
distinct because their existing score layouts and distance rounding differ.
Both prefer a start observed within the horizon, then admission-to-start wait,
then distance and their existing stable ties. Dynamic candidates without an
in-horizon start use distance before the existing insertion ties.

Only execution functions change live assignments, routes, inventories, jobs or
event queues. Plant and transport helpers can also execute on a private forecast
state, where their mutations and events remain local to the candidate. The
forecast module contains context building and predictions. The routing module
contains observation building, alternative construction, scores and selection.

## Data structures and dependencies

Important dictionary records have `TypedDict` definitions beside the existing
state dataclasses in `state.py`. They describe field names and types for editor
assistance; dictionaries, field insertion order and serialization are unchanged.
No runtime constructors/conversion layer or physical state class hierarchy was
added. `Record = dict[str, Any]` remains for versioned scenario dictionaries,
validation adapters, event envelopes and structures not yet annotated.
Required/optional-key inheritance uses standard-library Python 3.10 typing.

| Record | Important fields and units |
| --- | --- |
| `ProductDefinition` | `id`, `origin`, absolute `release_time`, `disposition`, ordered `operations` (operation IDs, distinct from station IDs). Generated products also have `batch_id`/`quality_class`; these metadata keys are optional for execution inputs. |
| `ReturnBatch` | `id`, absolute `release_time`, integer `batch_size`, `product_ids`; network preparation adds `origin` and point prefixes. |
| `ProductState` | `state`, current `location`, zero-based `next_operation`, assignment/destinations, sparse absolute `milestones`, completed-job `history`, cumulative waiting-stock `inventory_wait` in time units. |
| `VehicleDefinition` | `initial_site`, `speed` (distance/time), integer payload capacity, `maximum_range` (distance or `None` for unlimited). |
| `VehicleState` | Load IDs, status, last arrived `location`, active leg, cumulative actual `distance` and unload wait. Position/range/prefix keys exist for finite-range or dynamic vehicles; route/version/serial keys only for dynamic execution. The actual mid-leg position comes from `position`/leg travel, not the last site. |
| `FixedLeg` / `DynamicLeg` | Target, origin/target coordinates, absolute departure/arrival times, full leg distance and departure load. Fixed legs require an origin site; finite ones add departure range. Dynamic origins can lie mid-leg, require `leg_id` and departure range (`None` for unlimited), and do not have an origin-site key. |
| `Operation` | Product/plant/station/operation IDs, zero-based operation index, absolute start/end and fitting availability window. Processing duration is `end_time - start_time`. |
| `PlantContainers` | Inventory maps product IDs to absolute entry times; `held_slots` reserves return capacity; waiting-AV values are arrival times; workstation values are active jobs or `None`. |
| `RouteStop` | `site_id`, pickup and delivery ID lists; both lists may be empty for a reset visit. |
| `PolicyRequest` / `AwarePolicyRequest` | Released transport/ownership facts. Only the aware shape adds required `operations`; neither shape exposes individual quality classes. |
| `PolicyVehicle` / `PolicySite` | Copied dynamic fleet position/range/routes with speed/capacity, and site coordinates/kind. Vehicle initial-site configuration is not part of this boundary. |
| `DynamicCandidate` | ID, vehicle/product/destination, insertion indexes, old/new routes, position/range, feasibility/reason, distances and lexicographic score. Rejected alternatives are retained; only feasible aware candidates get optional forecast fields. |
| `StaticCandidate` | Fixed destination, payload selection round, stop indexes and forecast; score/selected keys are appended during ranking. |
| `ForecastResult` / `DynamicForecastResult` | Absolute arrival/admission/first-start predictions and admission-to-start wait; admission/start are `None` if unobserved by the horizon. Static arrival can extend past the horizon. Dynamic results also include committed-delivery ETAs. |

All coordinates/distances use the same scenario distance units; all times,
processing/calendar durations and wait totals use simulation time units. Typed
records are applied incrementally, not a claim that the entire versioned engine
is statically checked. Existing validators and independent checks remain required.

`InventoryRunState.products[id]` tracks physical membership, assignment, next
operation index, milestones/history and accrued wait. `vehicles[id]` tracks load,
actual position, remaining range, active leg and remaining stops. Local plant
containers hold `inventory` (product ID to entry time), `held_slots`,
`waiting_vehicles` (arrival times) and `workstations` (active operation or None).
All inventory plants have authoritative containers keyed by plant ID. The primary plant
aliases these containers for saved-format compatibility; aliases are not extra
physical stock. `state.local_plant_view()` intentionally shares those containers,
products, vehicles and event lists. A view shares whichever state it receives:
live execution or an already isolated forecast copy. Its numeric clock is copied
when created. It is never itself a forecast-isolation boundary.

`state` depends only on the standard library. `transport` depends on state;
`plant` depends on state/transport. Inventory execution uses the same plant settlement
for fixed routes, dynamic routes and private dynamic projections.
`routing` uses forecasting; dynamic forecasting lazily imports the engine only
when projecting copied state with dispatch disabled. This local import avoids
an import cycle and recursive dispatch. The independent verifier imports none
of these modules: its repeated rules are deliberate audit calculations.

The dynamic core retains the primary singular-site alias and the existing
single-product engine because removing them would alter saved formats or physical
execution during a structural refactor. Conversions are concentrated in config;
there is no new class hierarchy or routing framework.

## Generation and validation

`generator.prepare_scenario(path, seed)` reads either supported configuration
shape. Format two takes arrival settings from the top-level `generation`; format
three takes them from each point. Both call `generate_return_batches(settings, generator)`
with the arrivals/batches stream. `generate_products(batches, origin, quality_classes,
overrides, has_recycler, generator)` applies the point's quality weights and uses
its separate quality stream. Network preparation prefixes the resulting local IDs
and sorts the combined records before canonical serialization.

Both preparation exits validate before calling the private
`generator._canonical_resolved_scenario()`. It preserves `json.dumps` defaults,
`indent=2`, `sort_keys=True`, `allow_nan=False`, one final LF and UTF-8 encoding.
It then decodes those exact bytes, establishing nested dictionary insertion order
for subsequent events as well as returning the bytes used for hashing. The JSON
round trip is intentional; runner and experiment file writers have different
ordering/overwrite contracts and are not reused here.

The format-specific validators in `config.py` read the actual origin IDs, initial
facilities, coordinates and calendars. For generated geometry they check the bounds.
For generated calendars they validate global settings merged with station overrides.
Remanufacturing routes must be available at every eligible plant, so the validator
checks them against the intersection of the plants' operation IDs. Formats two
and three allow adjacent windows. validate_scenario rejects format one, missing
format declarations and unsupported formats before runner creates output.

## Fixed definitions and forecast ownership

A fixed or dynamic inventory run builds `ScenarioDefinitions` from its execution
scenario. The ID maps reference the original product, vehicle, plant and site
declarations. Engine helpers read those references for lookups and keep the
existing declaration order or explicit sort for scheduling. Product progress,
positions, loads and jobs live in `InventoryRunState`. The index belongs to the
execution and stays outside its scenario, physical state and dispatch records.

The dynamic observation builder selects outstanding released requests and emits
the fields allowed by the policy. Forecast-context builders select released
product definitions and call `physical_snapshot(state, released_only=True)`.
The snapshot copies the selected products, fleet, workstation jobs, inventory,
held slots, unload queues and populated plant containers in their saved field
order. Static forecasting also reads departure events to count each trip's
progress. Event and decision histories are excluded from the snapshot before copying.

A candidate runs on its own copy of the recorded physical snapshot. The dynamic
projection restores primary-plant aliases and prepares its index from the
released-only forecast scenario. Final export uses `released_only=False` to
include future products. Both retained execution paths export this same physical
snapshot contract, preserving the existing single-plant and network layouts.

## Rules that must not change during structural edits

- Unaware boundaries expose release/location/disposition and physical fleet/site
  data, excluding individual quality and operation lists. Aware boundaries add
  required operations and only **released** physical state for forecasts.
- Insertions preserve pickup-before-delivery, payload, all commitments, actual
  position/range and facility escape feasibility. Stable tie-breaks and rounded
  distance scores are unchanged. Aware ranking prefers a first start within
  horizon, then admission-to-start wait, then added distance. Without a forecast
  start, distance decides before the existing stable ties.
- Forecast copies include waiting inventory, held slots, active work, availability
  windows and committed deliveries. Replacing a route updates its deliveries'
  ETAs in the projection. No unreleased future returns may influence a decision.
- Intermediate work reserves its return slot; final-operation start frees it.
  Settle admissions before another station start so waiting AVs get newly freed
  space in arrival-time/vehicle-ID order. Full plants leave products onboard.
- Range consumption uses actual traveled prefixes once, including cancelled legs
  and the final partial leg. Any actual facility arrival resets finite range,
  including empty visits/blocked unloading. Collection points never reset it.
- A rerouted leg's superseded arrival token is discarded **before clock advance**;
  it causes no arrival, unloading or reset. Same-time event sequence is meaningful.
- Stream namespaces, draw ordering, JSON field ordering and original scenario
  bytes are preserved. Saved fixture scenario files stay pinned to LF.

## Concrete reroute and replay trace

The historical `tests/fixtures/checkpoint_4b/scenario.json` has RF1 `(0, 0)`,
CP1 `(4, 0)`, CP2 `(2, 1)`, speed 1 and range 10. At time 5,
`transport.advance_vehicle_travel(..., 5)` accounts the first unit of the return leg:
AV1 is `(3, 0)`, carries P1 and has range 5.

Under `dynamic_quality_aware`, the time-5 decision follows these calls.

1. The engine handles P2's release, emits dispatch event 12 and calls
   `dispatch_dynamic_requests()`. Its observation contains P1's delivery commitment
   and the currently active RF1 leg with arrival time 8.
2. Routing enumerates three unique alternatives, using actual position/range.
   For the direct diversion, it inserts CP2 before RF1 and merges delivery of P1
   and P2 at RF1. Remaining distance is `sqrt(2) + sqrt(5) = 3.6502815398728847`;
   added distance over the current RF1 leg is `0.6502815398728847`.
3. Forecasting copies the physical context for each feasible alternative. The
   diversion replaces the old RF1 leg in that copy. Both deliveries are predicted
   at `8.650281539872886`, so P1's committed arrival uses the revised ETA. P2's
   predicted admission is the same time, its first start is `9.650281539872886`,
   and its inventory wait is 1.
4. Routing adds the score prefix `[0, 1.0, 0.650281539873]` and stable vehicle,
   destination and insertion ties. It selects candidate `C00001` and returns
   the proposal and candidate audit, leaving the observation and live state intact.
5. The engine logs the decision, assigns P2, cancels leg `AV1:L2` at the actual
   position and installs the CP2/RF1 route. Its stale arrival at 8 is discarded.

Open the record with `time == 5` in `dispatch_decisions.jsonl`. It records trigger
`released_products == ["P2"]`, `last_event_sequence == 12`, the original fleet
observation, all candidate predictions/scores and the selected proposal.

The saved record prefix at event 16 shows the revised leg. Event 17 is CP2
arrival at `5 + sqrt(2)`, and event 18 is P2 pickup. Event 21 is RF1 arrival at
`5 + sqrt(2) + sqrt(5)`; admission and processing follow. Event 29 is P1's first
completion. The viewer can seek event 29, then Previous event to restore the
active operation and full remaining sequence. Same-time Next event steps reveal
completion, terminal state and P2's next start separately. Availability bars are
static saved windows; orange processing overlays use only that displayed prefix.

## Tool entry points and dependencies

| Tool | Main entry points | Dependencies and ownership |
| --- | --- | --- |
| Verification | `verify_run(folder)`, `main()` | Standard library only. Own audit state and findings, independently reconstruct generation, physical transitions and policy evidence, then write reports. |
| Replay | `RunReplay(folder)`, `snapshot(time, events_applied=None)`, `ReplayController` | Standard library only. Read scenario/manifest/events; own physical checkpoints and an immutable completed-event index. Each snapshot is a private copy. |
| Viewer | `open_viewer()`, `MatplotlibViewer`, `main()` | Replay plus optional Matplotlib. Own artists, widgets and a controller. Write only an explicitly requested PNG. |
| Experiments | `run_experiments()`, `refresh_verification()`, `main()` | Scenario preparation/validation and subprocesses. Own manifests and exports, launch simulation/checker children, retain failed attempts. |
| KPI export | `measure_run()`, `export_kpis()`, `main()` | Standard library only. Independently integrate saved event intervals into local counters and CSV rows. Read the saved verification label. |

The verifier imports no simulation or replay modules. Its route, generation and
physical calculations are independent audit rules. Replay applies recorded
transitions for display and has its own acceptance rules. A replay snapshot does
not decide whether evidence passes verification. The small JSON-reading helpers
in verifier, KPI export and experiments retain their tool-specific validation and
failure messages. This also permits copying either `tools/verify.py` or
`tools/kpis.py` outside the project and running it with standard Python.

For network generation auditing, each per-point proxy deep-copies the retained
declaration graph after excluding only top-level `products` and `arrival_batches`.
Those lists are then replaced by the same point-filtered, prefix-stripped records
as before. Retained quality definitions stay owned by that audit view, including
routes used in expected diagnostics; observed replacement routes keep their
existing borrowed references. Source evidence is read only. No simulation helper
or shallow-copy substitution is used. This removes discarded copying work; no
runtime or allocation improvement is claimed without separate measurement.

In KPI export, `read_event_records()` parses a borrowed event stream, preserving
physical line numbers and contiguous sequence checks. `measure_run()` owns the
stream, checks times and accumulates the preceding interval before applying each
event. Its `PlantTotals` objects own waiting/held/busy sets and integrals.
`apply_inventory_event()` changes only the selected local accumulator.
`export_kpis()` owns discovery, skipped-run diagnostics and CSV writing.

Experiment comparison building preserves attempt order and changes only the
inclusion flags. summarize_policy_runs() reads one condition/policy group and
returns its attempt counts and matched-replication statistics. Raw exports retain
every attempt, while paired exports and summaries require complete verified groups
with matching input hashes. Refresh launches a fresh verifier for current files
rather than trusting an older report.

Six test classes share `tests.verifier_cli.verify_run_via_cli(test_case, directory,
expected_exit)`. It runs the same root compatibility command with the existing
interpreter/options and inherited cwd, asserts child exit with stdout/stderr,
then reads and checks the report exit. Test callers invoke it directly; their
scenario setup, independent results and stricter freshness/standalone helpers
remain separate. `tests/__init__.py` makes that import reliable for discovery
and direct unittest module invocation. This helper is not a production dependency.

## Saved artifacts

All paths below are generated locally under the requested output directory.
The single-run runner writes its six evidence files. Tools retain their own
explicit writing responsibilities.

| Artifact | Purpose | Readers |
| --- | --- | --- |
| `scenario.json` in a run | Exact resolved inputs and seed provenance, with byte identity recorded in the manifest | Resolved simulation rerun, verifier, replay/viewer, KPI export, experiment inspection |
| `run_manifest.json` | Selected policy, input/output formats, scenario hash and completion status | Verifier, replay/viewer, KPI export, experiment inspection |
| `events.jsonl` | Ordered physical events with simulation times and sequence numbers | Verifier, replay/viewer, KPI export |
| `dispatch_decisions.jsonl` | Permitted observations, candidates, scores and selected decisions | Verifier and manual policy inspection |
| `summary.json` | Saved horizon totals | Verifier, KPI consistency checks, experiment inspection |
| `final_state.json` | Saved physical horizon state and summary | Verifier and manual inspection; KPI run discovery uses its presence |
| `verification.json` | Current verifier report, findings, recalculated totals and exit code | Experiments, KPI status labels, manual inspection |
| `verification.txt` | Human-readable verification report | Manual inspection |
| Explicit `*.png` | Requested viewer snapshot | Image viewer/manual inspection |
| Explicit KPI `*.csv` | One row per readable completed run, UTF-8 with BOM | Excel or analysis software |
| `conditions/C*/configuration.json` | Experiment's original condition bytes | Scenario preparation and manual inspection |
| Experiment replication `scenario.json` | One frozen generated input shared across policies | Child simulations and experiment hash checks |
| `experiment_manifest.json` | Seeds, attempts, exact child commands, statuses, comparisons and exclusions | Refresh, exports and manual reruns |
| `results.csv` | All experiment attempts and available metrics | Analysis software/manual inspection |
| `paired_results.csv` | Complete verified policy groups with replication IDs | Matched-policy analysis |
| `comparisons.csv` | Group completeness, matching hashes and missing policy names | Analysis software/manual inspection |
| `summaries.csv` | Condition/policy means and sample standard deviations for matched groups | Analysis software/manual inspection |
| `logs/*.simulation.stdout.txt`, `*.simulation.stderr.txt` | Retained child simulation output and errors | Manual debugging |
| `logs/*.verification.stdout.txt`, `*.verification.stderr.txt` | Retained checker output and errors | Manual debugging |

Experiment JSON exports are replaced atomically using temporary `.tmp` files.
CSV exports replace their explicit destinations. A new simulation or experiment
requires a fresh output folder. Verification replaces its two reports. Replay
reads evidence, and viewing writes a file only for an explicit render request.

## KPI quantities and denominators

Event integration observes `[0, horizon]`. Each event changes the state for the
following interval, while instantaneous occupancy contributes to the peak.
Cancelled legs stop contributing at cancellation and revised legs begin at their
recorded departure. Unfinished travel, stock, unloading and processing contribute
through the horizon event. Missing terminal evidence makes the run unreadable for
export. Ratios with zero denominators are blank in CSV.

| Quantity | Accumulation and denominator |
| --- | --- |
| AV travel time and distance | Sum active-vehicle time, and speed times active time. Units are vehicle-time and distance. |
| AV unload wait | Sum waiting-to-unload vehicle-time. |
| Mean release-to-pickup | Sum waits of collected parts divided by pickup count. Parts still awaiting pickup contribute to stock and the horizon count. |
| Mean collection-point inventory | Total waiting product-time divided by horizon. |
| Plant inventory wait and mean waiting stock | Waiting product-time, and that area divided by horizon. |
| Mean/peak occupied slots | Waiting plus reserved slots. Integrate slot-time over horizon for the mean and observe each post-event peak. |
| Capacity use percentage | Occupied slot-time divided by capacity times horizon. Global capacity use requires every plant's capacity to be finite. |
| Plant busy percentage | Time with at least one active station divided by full horizon, times 100. Global mean divides summed busy time by plant count times horizon. |
| Workstation utilization | Summed processing time divided by summed scheduled available workstation time inside the horizon, times 100. Each station's overlapping windows count once. |

Verification audits consistency within its supported scope. KPI export calculates
these metrics and preserves the saved status label, including `NOT_VERIFIED` when
no report exists. A label describes the saved report. A fresh verifier invocation
checks evidence after any subsequent modification.

## Where to make a change

| Intended change | Start here |
| --- | --- |
| Return batches, quality sampling, coordinates or calendars | `simulation/generator.py`, with declaration checks in `config.py` |
| Candidate alternatives, eligibility, policy scores or ties | `simulation/routing.py` |
| Predicted admission and processing | `simulation/forecasting.py` and its private physical snapshots |
| Event timing, dispatch boundaries or horizon handling | `simulation/engine.py` |
| Pickups, travel, range or unloading | `simulation/transport.py` |
| Capacity, reservations, workstation starts/completions | `simulation/plant.py` |
| Display reconstruction and seeking | `tools/replay.py` |
| Viewer layout, controls or drawing | `tools/viewer.py` |
| Event-based KPI calculations or CSV columns | `tools/kpis.py` |
| Matched experiments, attempts and exclusions | `tools/experiments.py` |
| Independent supported evidence checks | `tools/verify.py`, with independent rules and tamper tests |

Historical checkpoint notes and refactor acceptance records live under
`docs/history/`. They describe the implementation at their recorded revision.
README.md and this guide describe the current commands and boundaries.

## Dynamic facility service and the F1 correction

On `simulation-v8-fix-delivery-service`, every dynamic admission or recycler
acceptance must satisfy all three conditions: the vehicle has actually arrived at
the current head stop, the product is onboard and destination-matched, and that
stop's `deliveries` explicitly contains the product ID. Sharing a destination does
not authorize unloading at an earlier visit. Eligibility preserves recorded pickup
order within the authorized subset.

`transport.facility_delivery_products()` reads that permission for both plant
retries and recycler service. Its returned list and local immutable ID set are
owned values. The authoritative route is owned by the vehicle's physical state;
its current stop remains in place until authorized service finishes. Waiting AVs
are excluded from candidate insertion, so replanning cannot replace their pending
permission. No separate mutable authorization cache, new saved field, or reference
to a routing candidate is retained. Every retry uses the same current visit even
when its products enter inventory at different timestamps.

After all current deliveries are serviced, the wait ends and the stop can complete
while future load remains onboard. Those products keep their AV ownership, planned
destination and later route obligations. Capacity/held-slot checks and arrival-time
then vehicle-ID queue priority are unchanged. A final-operation start still frees
space before another station starts. Range resets still occur at actual facility
arrival before admission, including blocked and empty visits. Static final-trip
service retains its former destination-based behavior. The original scalar
format-one implementation was later retired; its historical evidence is preserved.

Dynamic candidate projections use these same physical helpers on their private
state with dispatch disabled. Corrected admission times can change aware scores
and selections without changing the ranking rule. Released-only information,
future-return exclusion and unaware forecast guards remain unchanged.

The standalone verifier independently filters its projected and replayed dynamic
service by the head stop. It does not import simulation code. A new violation rule,
`dynamic.stop_authorized_delivery`, reports the authorized facility/product IDs,
observed delivery, event sequence and time before applying an unauthorized
`plant_admission` or `recycler_acceptance`. Existing physical/ownership checks,
report layout and PASS/FAIL/INCOMPLETE exit codes remain. The delivery-service
change itself left the other audit findings separate; the verifier-only F2/F3
corrections are described below. At the delivery-service revision, assertion and
arithmetic-representability findings were still unresolved; F4/F5 are fixed in the
current implementation as documented below. Replay and KPI integration end a visit's unloading wait
when its own deliveries finish, even when same-destination future load remains.
KPI formulas/CSV fields and viewer controls/appearance are unchanged.

### Historical cause and regression evidence

Untouched stage 5 (`c71665a30d55ca8a90ce159c245f3485f6dbb4ed`) reproduces the
stage-4 seed-54 `KeyError: 'CP3:P000001'`. At time 4.200213024643472 AV2's first
RF2 visit authorizes CP3:P000003, but destination-only unloading also admits
CP3:P000001. The latter was committed to a subsequent RF2 visit. When it starts
processing, it correctly disappears from the outstanding transport observation,
while the route still names its delivery. A release at 6.045381813131638 exposes
the stale obligation in route feasibility. The fix preserves that request filter
and feasibility check; it does not catch/skip the missing lookup or delete a valid
future commitment.

A small explicit regression has RF1 `(0,0)`, CP1 `(1,0)`, speed one and payload two.
P1 is released at zero; P2/P3 at 1.5. Both policies use distance fallback with empty
workstation calendars. The time-1.5 insertion carries P1 to CP1 for P3, then RF1
for P3 at three, CP1 for P2 at four, and RF1 for P1/P2 at five. Before the fix,
the first RF1 visit admits P1 too; after it, P1 stays onboard. A P4 release at 3.5
triggered KeyError on both baseline policy paths; it now replans successfully.
At horizon 3.25 the precise regression asserts P1 still onboard rather than merely
checking that execution does not raise.

The held-slot case admits authorized P3 at two and P4 at four, retaining later-stop
P1 until six. Forecasts independently predict P2 admission/start at two/three, or
six/seven with blocked service. The recycler projection collects P2 at one, visits
RC1 at two, and accepts P2 only on its authorized visit at four; the old physical
forecast incorrectly predicted acceptance at two. Ten new tests exercise these
rules, tampered acceptance and original seed 54. The baseline suite passed 195
tests; the corrected suite passes 205 with optional plotting installed.

Choose fresh folders to execute seed 54 with identical resolved inputs under both
policies. This command fails on preserved stage 5 and succeeds on the fix branch.

```powershell
$checkRoot = Join-Path .\results ("delivery-54-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$unawareRun = Join-Path $checkRoot 'unaware'
$awareRun = Join-Path $checkRoot 'aware'
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 54 --policy dynamic_quality_unaware --output $unawareRun
if ($LASTEXITCODE -ne 0) { throw 'Unaware run failed' }
& $thesisPython -m tools.verify $unawareRun
if ($LASTEXITCODE -ne 0) { throw 'Unaware verification failed' }
& $thesisPython -m simulation --resolved-scenario (Join-Path $unawareRun 'scenario.json') --policy dynamic_quality_aware --output $awareRun
if ($LASTEXITCODE -ne 0) { throw 'Aware run failed' }
& $thesisPython -m tools.verify $awareRun
if ($LASTEXITCODE -ne 0) { throw 'Aware verification failed' }
```

The historical stage-5 seeds-42–61 workflow stopped after 25 verified runs; its
seeds-1–20 workflow completed 40. Those historical outcomes remain recorded in
docs/history/. On the fix branch, all 40 seeds-42–61 paired runs pass independently
and export 40 KPI rows with zero skips. Both seed-54 repeats match all five physical
artifacts byte-for-byte. Scaled seed 42 (only each point's arrival rate multiplied
by two/four) passes for both policies, including the two former unaware crashes.

### Cross-version acceptance

The untouched stage-5 checkout and fix used the same CPython 3.12.14 interpreter.
The established 27-case matrix gives **121/135 identical artifact comparisons**.
All **23 unaffected cases match 5/5**, including all static/legacy cases, range,
held-slot/queue, reroute/horizon and seed-43 cases. All 27 manifest comparisons match
after independently validating/removing each output-folder `run_id`; scenario
hashes, policy, provenance, completion and every other field agree. All original
27 runs pass the old verifier, and all corrected 27 runs pass the new independent
verifier. No fixture was regenerated.

Four cases intentionally differ (the aware repeat is counted separately):

| Case | Matching artifacts | First meaningful difference and consequence |
|---|---|---|
| Pilot seed 42, unaware | scenario only, 1/5 | At 29.66619335673636 the old run admits CP1:P000002 at an RF1 stop that authorizes CP3:P000003. The fix retains CP1:P000002 onboard until 37.29764138357254. It finishes one operation before the horizon; unfinished count changes from one to two. |
| Pilot seed 42, aware | scenario and summary, 2/5 | At 15.07328780775355 the first admitted product changes from unauthorized CP2:P000001 to authorized CP3:P000003. Later admission/work histories and observations change; the aggregate summary happens to remain identical. |
| Pilot seed 42, aware repeat | scenario and summary, 2/5 | Same physical correction as the preceding row; it remains byte-identical to the corrected aware run. |
| Format-two recycling input run dynamically aware | scenario only, 1/5 | At decision time .5676968513057951, P000003 candidate C00003 previously predicted early admission 2.050827161545433/start 2.550827161545433 (wait .5). Correct service predicts its authorized later admission/start 4.050827161545433 (wait zero). Its added distance is one, so it beats C00001's zero wait/distance two. Later routes differ; recycled count changes from zero to seven. |

The recycling case shows why a previously successful run cannot automatically be
classified unaffected: even an unselected alternative's corrected prediction can
change the chosen plan. Rerun prior affected policy comparisons rather than mixing
old and corrected results. Original raw scenario files remain valid replay inputs;
their old result logs can fail the corrected audit.

The two scaled configurations generate 11 and 22 products. Corrected
unaware/aware remanufactured-versus-unfinished counts are respectively 7/4 and
11/0 at twice the rates, and 5/17 and 9/13 at four times the rates. These unfinished
counts reflect the fixed horizon, not failed runs. Seed 54 remanufactures all eight
products under each policy. The supported two-replication/four-policy experiment
also produces eight independently verified runs. Separate copies of verifier and
KPI tools run under isolated standard-library Python. Viewer rendering and controls
are exercised with Agg; an interactive Windows desktop was not tested here.

## Exact discrete evidence and tolerant physical values (F2)

The verifier's evidence-comparison fix retains physical absolute/relative
tolerances of `1e-9`. `comparison_difference()` selects semantics from saved field
roles and the three established score layouts. `check_value()` keeps existing
rule identifiers and event context. A discrete mismatch additionally records its
precise path in `ids.field` and reports the exact expected/observed leaf values.
No simulation, generation or routing calculation changes.

| Evidence role | Comparison |
|---|---|
| Seeds/provenance, stream maps and departed-leg counters | Type-aware exact metadata equality; arbitrary-size Python integers never pass through float. |
| Event sequences, counts, capacities, operation/selection indices, route revisions and numeric operation identities | Exact JSON integers; bool and integer-valued floats do not satisfy integer fields. Existing range restrictions remain. String operation identities remain supported. |
| Format versions | JSON integers in the existing supported scope; unsupported versions remain INCOMPLETE. |
| Score components | Static aware positions 0/4/5, dynamic unaware positions 3/4/5, and dynamic aware positions 0/5/6/7 are discrete flags/indices. Distance and waiting components remain continuous. |
| Times, coordinates, windows, distance, range, physical waits and forecasts | Existing tolerant physical comparison, including values written as integer JSON literals. |
| Product/vehicle/station ID map keys | Opaque identities. An inventory product called `sequence` still maps to a continuous entry time. |

On the delivery-fix baseline (`62e5bfa10ce761438aa4903ed8980093acbd2191`), a
coherently rehashed format-two scenario with a stream seed increased by one still
received PASS. A final event sequence increased by `1e-10` also passed. These now
produce FAIL with exact integer/sequence diagnostics. A 401-digit valid master seed
is retained without precision loss, harmless physical differences still pass,
and out-of-tolerance physical differences still fail. Original valid simulation
inputs/outputs can be re-verified; regeneration is unnecessary.

The original audit remains a historical review of stage 5. This verifier branch
retains F1 and fixes F2/F3. At that revision F4–F8 and D1 remained unresolved;
this branch additionally fixes F4/F5 as documented below.

## Malformed evidence and fresh reports (F3)

`read_evidence()` checks known object/list/operand shapes before passing records
to independent generation or replay. Focused helpers check declarations, state
containers, events, dispatch observations, candidates, proposals and forecast
contexts. They read borrowed JSON structures without modifying them. The rules
recognize format-specific differences, including format-two unaware decisions
without candidate forecasts and nullable legacy/range fields. These checks do not
import simulation validators, physics, routing or forecasting.

Provenance is validated separately from physical declarations. A list, string or
null in place of scenario/manifest provenance records an INCOMPLETE finding and
prevents unsafe metadata/generation access. Usable events and physical definitions
can still be independently audited. Stream-map depths and required independently
derived seed keys are checked before exact value comparisons. Incorrect present
integer seeds remain violations; missing/unusable seed evidence is incomplete.

A malformed dispatch/export artifact is explicitly marked unusable, with a finding
identifying its file and nested path. It is not replaced with empty records or
quietly dropped from a PASS audit. Other usable artifacts still undergo their
checks. Raw scenario-byte hashing is independent of structural/provenance validity,
so a proved hash violation is retained even when scenario declarations are unusable.

| Finding | Meaning |
|---|---|
| `scenario.seed_provenance` / `manifest.seed_provenance` | Required metadata is missing or has an unsupported nested shape; INCOMPLETE category. |
| `evidence.structure` | A known nested container/operand is unusable; INCOMPLETE, with the file and failing path. |
| `evidence.read` | Existing missing/unreadable/parse diagnostics; INCOMPLETE. |
| `generation.evidence` | Existing generation-evidence boundary, including missing independently required seed-map keys; INCOMPLETE. |
| Existing comparison rules, e.g. `events.sequence`, `generation.seed_provenance`, `manifest.scenario_sha256` | Usable evidence proves a contradiction; violation category and FAIL. Discrete leaf diagnostics include `ids.field`. |

The classification order stays FAIL/1 if any violation is proved, otherwise
INCOMPLETE/2 if required checks cannot finish, otherwise PASS/0. Both finding
categories are retained when they coexist. Unexpected programming errors are not
blanket-caught as evidence problems. The only special arithmetic conversion guard
preserves the old rejection of unusable physical operands while allowing large
integer seeds; this does not change the simulation's numerical envelope.

Every classified verification in a writable run folder rewrites verification.json
and verification.txt. Existing reports are never used as authority. Repeating the
same malformed evidence yields the same report bytes; no timestamps or report
schema changes were added. Report-writing I/O failures still propagate from the
API and produce an explicit nonzero CLI result, without claiming successful refresh.

### Reproductions and acceptance

The remote baseline was the unchanged delivery fix,
`62e5bfa10ce761438aa4903ed8980093acbd2191`. Before editing, all 205 tests passed.
The F2 regressions failed on that baseline because numeric tolerance admitted
discrete alterations. F3 regressions demonstrated both AttributeError/stale-report
paths and malformed structures incorrectly treated as ordinary value comparisons.

| Coherently prepared counterexample | Delivery-fix baseline | Corrected verifier |
|---|---|---|
| Format-two arrivals_batches stream seed +1, matching manifest provenance and recomputed scenario hash | PASS/0 | FAIL/1; `generation.seed_provenance`, exact seed values and field path. |
| Final event sequence +`1e-10` | PASS/0 | FAIL/1; `events.sequence`, expected integer, observed fraction, time and record context. |
| Scenario and manifest provenance both `[]`, matching hash, no prior report | AttributeError, exit 1, no report | INCOMPLETE/2 with fresh JSON/text reports. |
| Same malformed provenance with previous PASS | AttributeError, exit 1, old PASS left untouched | INCOMPLETE/2 replacing the previous PASS in both files. |
| Valid run with a 401-digit master seed | INCOMPLETE/2 from inappropriate int-to-float finite checking | PASS/0 with all original simulation artifact bytes retained. |

The final suite contains 228 tests: the prior 205 plus eleven F2 and twelve F3
tests. F3 includes 36 combinations of scenario/manifest/both provenance,
list/string/null, and no prior report/PASS/FAIL/INCOMPLETE. Every combination is
repeated, checking stable fresh reports. Additional cases cover generated formats,
nested stream maps, candidate/proposal/state/event shapes, raw hash precedence,
combined proved violations, explicit write failures and experiment/KPI integration.

The established 27 cases match all **135/135** scenario/event/dispatch/summary/
final-state bytes and all **54/54** valid JSON/text verification reports against
an untouched baseline checkout. Manifests are compared field-for-field after
validating each output folder's run_id. All four policies and three formats remain
represented. Seed 54 under both dynamic policies uses identical resolved bytes
and retains the F1 physical outcomes; the original delivery-service tests and
`dynamic.stop_authorized_delivery` tamper checks remain strict.

The two seed-54 runs and eight initially valid experiment runs additionally match
50/50 simulation artifacts and 20/20 report files against the baseline. A deliberate
malformed dispatch edit is then refreshed in one experiment member: all eight
attempts remain recorded, that run becomes INCOMPLETE, its four-policy group is
excluded, and the other replication remains eligible. KPI export writes eight
rows with zero skips, seven PASS labels and the one freshly written INCOMPLETE.
Standalone `python -I verify.py`, preferred module and compatibility commands are
exercised on saved evidence. Valid inputs and tool behavior were checked with
CPython 3.12.14; no Windows desktop interaction is claimed.

Saved valid simulations can be re-verified directly. No input/output regeneration
is required for F2/F3. A prior PASS is only a saved label until the current evidence
is rechecked. Experiment refresh records INCOMPLETE for malformed evidence, keeps
the attempt and diagnostic, and excludes its entire matched group. KPI export
continues reading that refreshed status under its existing contract; it does not
perform verification or turn an INCOMPLETE label into PASS.

## Always-active physical guards (F4)

Required physical checks use `require_physical_state()` and raise
`PhysicalStateError`, a `ValueError` subclass caught by the existing simulation
CLI. Diagnostics contain simulation time, rule and relevant entity IDs with
expected/observed state. The helper reads live state or the caller's private
forecast copy; it never writes events or changes state. Failures propagate;
there is no optimization-dependent or forecast-specific bypass.

The following table records the **historical F4 conversion before format-one
retirement**. All 21 assertions at that verifier-evidence baseline were mandatory;
scalar-only transitions and their checks have since been deleted together:

| Module | Checks replaced | Classification |
| --- | --- | --- |
| `transport.py` (9) | Active arrival at its clock, legacy pickup ownership/location/empty load, legacy admission, remanufacturing destination/disposition, recycler facility/disposition/destination | Transition preconditions and physical invariants |
| `plant.py` (9) | Inventory start, active completion at its clock, local/network occupancy, conservation and payload | Transition preconditions and physical invariants |
| `engine.py` (3) | Dynamic pickup ownership/origin, legacy scheduled station/window | Transition preconditions |

None was development-only; no production assertions remain. Explicit guards also
check physical pickup location/payload before moving a part, onboard ownership on
admission/acceptance, and inventory/processing ownership on start/completion.
Conservation counts load occurrences, so duplicate entries cannot conceal missing
physical membership. F1 stop authorization remains active during every retry.
These changes do not redesign settlement, routes or container ownership.

The new corrupted-state probes call actual APIs under ordinary Python, `-O` and
`PYTHONOPTIMIZE=1`. Before the fix, the recycler probe raised `AssertionError`
ordinarily but recycled a remanufacturable product under optimization. The new
contract rejects both with `PhysicalStateError` before acceptance. Other probes
cover pickup ownership/location, payload/plant capacity, duplicate/missing
membership, invalid processing, and an unauthorized dynamic visit. These are
deliberately invalid internal states, distinct from accepted end-to-end inputs.
The historical F4 validation compared valid legacy, static aware and both dynamic
policy examples across all five
physical artifacts under ordinary and optimized execution and pass independent
verification. Existing assertion-raising mocks in tests remain intentional tests
of forbidden forecast calls, rather than production checks.

## Representable physical arithmetic (F5)

Finite declarations alone do not guarantee finite derived values or advancing
physical time. `simulation/numerics.py` checks existing arithmetic without
changing its normal algorithms or expression order. It reads values and returns
them unchanged; no helper changes state, writes files, records events, samples
randomness or knows product quality. Its `PhysicalArithmeticError` is a
`ValueError`, distinct from an ordinary infeasible route or absent processing
opportunity. Errors propagate through live execution and private forecasts;
routing does not silently drop an alternative with broken arithmetic.

| Helper / callers | Contract |
| --- | --- |
| `finite_physical`, `nonnegative_physical` / travel, plant, engine, state totals, routing | Finite derived amounts; distance/wait totals must also be nonnegative. Keep sum/add ordering; do not coerce integer metadata. |
| `physical_distance` / transport, engine, routing, fixed forecasts | Existing `math.dist`; finite result and nonzero geometry for distinct positions. |
| `physical_end` / the next two helpers | Finite start, duration, end; positive duration must produce `end > start`. No epsilon or rounding. |
| `travel_arrival` / departures, route feasibility, fixed forecasts | Positive finite speed, finite distance/speed, advancing arrival for positive distance. Zero distance permits a same-time arrival. Finite arrival beyond the horizon is valid. |
| `processing_completion` / window fitting, starts, copied active jobs | A positive operation must have a finite end strictly after its start. Compute before removing stock or recording a start. |
| `travel_prefix` / tracked live and forecast motion | Check elapsed-time multiplication before capping by leg distance, then return the existing actual-travel prefix. Reject overflow/underflow; do not hide infinity inside `min`. |

`check_insertion_route` now accepts keyword-only `departure_time` (default zero
for existing direct callers); enumeration supplies the decision clock. It checks
deterministic transport time along every candidate without plant forecasts or
additional policy information. Range/payload violations still return the existing
feasibility reasons. Numerical representability errors raise instead. No score,
tie, trigger or candidate field changes. Static trip progression remains fixed.

A closed calendar, no matching inventory, or a busy station does not attempt a
new operation. Fitting is requested only after an eligible idle station has work;
this avoids rejecting an unused workstation merely because a hypothetical job
at that clock would be unrepresentable. Ordinary whole-duration window fit,
exact-closing completion and inclusive-horizon execution remain intact. A fitting
calculation itself must have a representable end. Active legs/jobs supplied to
private forecast execution are checked before they enter its heap, using their
original start and configured speed/duration. Candidate copies remain isolated.

There is no arbitrary numerical envelope. Very small quantities are allowed when
all required calculations remain representable (for example a distance and speed
of `1e-200`, giving a one-unit trip). Very large integer seed metadata never goes
through these physical guards. A nonzero distance whose travel duration rounds
to zero, a positive duration lost when added to the absolute clock, or a total
overflow is rejected explicitly. This intentionally changes pathological inputs;
it does not clamp infinities to the horizon, add delays or complete work instantly.

The independent standard-library verifier implements its own geometry, arithmetic
and positive-progress checks. It imports no simulation helper. Before replay it
checks attempted departures/starts against the configured speed/duration and
requires strict progress in declared endpoints and actual arrival/completion
events against their independently reconstructed start, even when the difference
would be within the ordinary physical comparison tolerance. Forecasts, route
feasibility, motion and accumulated totals independently check derived finiteness.
The retired scalar audit used a squared-distance expression with a fallback.
Retained inventory/network checks continue using their independent `hypot`
calculations and positive-progress checks. New violation rules are:

- `physical.finite_calculation`: a required derived physical quantity is nonfinite.
- `physical.distance`: distinct positions lost their positive distance.
- `physical.travel_progress`: positive travel fails to advance, including underflow.
- `physical.processing_progress`: positive processing fails to advance.

`PhysicalEvidenceError` carries report-safe expected/observed values and event
context to `verify_run`; a proved contradiction is FAIL/1. Unusable/nonstandard
JSON and malformed declarations keep their existing INCOMPLETE/2 handling.
F1 stop authorization, F2 exact integers and F3 fresh-report/FAIL precedence are
retained. No physical tolerance constant or report schema changes. Existing valid
runs can be re-verified without regeneration. Failure-artifact redesign remains D1.

### Counterexamples and regression evidence

The untouched verifier-evidence baseline (`92cfead8179c60061610c8b217e44e1fa29cac46`)
passed 228 tests. Baseline direct probes and temporary configurations established:

| Case | Baseline | Corrected contract |
| --- | --- | --- |
| Remanufacturable load at recycler | Normal Python: AssertionError; `-O`: accepted/recycled | Both reject with contextual PhysicalStateError before acceptance. |
| Distance 1, finite speed `1e-320` | Travel becomes infinity; fails later writing JSON | PhysicalArithmeticError at travel duration, before departure. |
| Finite coordinates `-1e308` and `1e308` | Infinite geometry; fails later writing JSON | PhysicalArithmeticError at distance calculation. |
| Distance `1e-320`, speed `1e308` | Positive-distance travel takes zero time; verifier PASS | Reject duration underflow; saved old evidence FAIL/1 (`physical.travel_progress`, event 3). |
| Departure `1e16`, distance/speed 1 | Arrival equals departure; verifier PASS | Reject nonadvancing arrival; old evidence FAIL/1 (`physical.travel_progress`, event 3). |
| Coincident sites, start `1e16`, duration 1 | Instant remanufacturing; verifier PASS | Reject nonadvancing operation; old evidence FAIL/1 (`physical.processing_progress`, event 9). |

The final 20 numerical regression tests produced 22 failing subcases and no
unexpected exceptions on the untouched baseline. Corrected tests include legacy, all four
inventory policies, positive small durations, real zero-distance legs, exact
window/horizon boundaries, partial travel, a future closed calendar, unused
stations, copied/resumed forecasts, overflow of individual completions and
accumulated distance/wait, and independent zero-time rejection. Guard probes use
unittest outcomes or printed results in subprocesses, not assertions removed by
optimization. Generated probes and run evidence remain outside Git.

Historical F4/F5 audit observations describe earlier revisions. At the physical-guards
revision, F6–F8 performance work and D1 remained unresolved; the following sections
describe the scoped F6–F8 improvements. D1 remains outside this branch.

### F4/F5 acceptance checks

Python 3.12.14 on Linux: the baseline suite passes 228 tests. The final suite
passes 256. The eight invariant tests include 30 corrupted-state subprocess
outcomes across ordinary, `-O` and `PYTHONOPTIMIZE=1`; the 20 numerical tests
also pass with `PYTHONOPTIMIZE=1`. The ten delivery-service tests pass under `-O`.
Four representative legacy/static/dynamic runs each produce identical five-file
physical evidence in normal and optimized mode (20 comparisons); all eight
outputs pass independent verification. No production assertions remain.

The established 27-case matrix retains all four policies and all three supported
formats, pilot seeds 42/43, rerouting, range rejection/partial travel, recycling,
finite inventory/holds/unloading, empty fleet and unfinished work. Against the
untouched baseline all **135/135** physical artifacts match byte-for-byte. All
27 manifests match after individually checking run IDs against their own folder
names, and all 27 runs pass independent verification. Both valid report files
also match for every case (**54/54**). The baseline's 27 runs pass its verifier too.

Two seed-54 policy runs use the exact same saved scenario, and eight format-two
experiment runs pass verification. Those ten runs additionally match all 50
physical files, 20 valid reports and ten manifests against the untouched baseline.
Repeating seed 54 matches all ten physical files across both policies; seed-42
repeat evidence matches all five. Existing F1, F2/F3, unaware-forecast guards,
policy-order, standalone/compatibility, replay and viewer tests remain active.
An experiment refresh with malformed dispatch evidence retains all eight attempts,
replaces one stale PASS with INCOMPLETE and leaves only four rows in complete
paired comparisons; KPI export retains eight raw rows (seven PASS, one INCOMPLETE),
with zero skips. Preferred, compatibility and isolated standard-library KPI exports
of the two valid seed-54 runs match exactly, with two rows and zero skips.

Headless dynamic execution succeeds with Matplotlib/NumPy imports blocked.
At that historical physical-guards revision, the preferred viewer and the
then-supported compatibility alias rendered a saved seed-54 run with Agg. Viewer
tests cover nine workstation rows, processing overlays, same-time event order
and backwards stepping. No interactive desktop window was tested. Tracked pilot
configuration and historical input/fixture bytes remain unchanged; generated
evidence is outside the repository. These checks establish compatibility for
the exercised ordinary cases, not a proof over every floating-point input.

## Execution-owned prepared calendars (F6)

The physical-guards baseline already checks busy workstations and eligible stock
before calendar search. This optimization retains those checks and prepares
adjacency-merged calendars once, rather than rebuilding them at each fitting call.
`ScenarioDefinitions.calendars[(plant_id, workstation_id)]` contains frozen
`WorkstationCalendar` values whose original and merged bounds are tuples. Local
WS1 IDs at different plants never collide. There is no global cache.

`prepare_calendars()` returns a fresh execution-owned index. An aware routing
decision prepares one index from its permitted forecast context, then passes it
as a private keyword argument through forecasting into candidate execution.
Static aware candidates already share their decision's definition index. Each
projection owns its index dictionary and can share only immutable calendar values.
Before reuse, definition construction compares plant/station identity, original
window bounds and their numeric types. Changed scenarios/calendars cannot reuse
incompatible values, including an integer-to-float JSON representation change.
Fixed definition dictionaries are read-only throughout an execution.

Fitting retains original interval order and adjacency-only merging; it does not
add overlap merging or change validation. The selected window is a fresh list
with the original numeric types, preserving event evidence and preventing a
mutated event from changing prepared windows. Direct two-argument
`fitting_window()` callers retain on-demand merging. F4/F5 checks remain active.
Calendars never enter scenarios, physical state, policy observations, dispatch
records or final-state exports. Independent verification keeps its separate code.

`state.merge_adjacent_windows()` is the shared pure rule used on preparation
cache misses and by direct, uncached fitting. It returns owned tuples without
sorting, coercion or overlap union. Original bound types still govern prepared
calendar reuse. Independent audit calendars and KPI availability unions retain
their different contracts and implementations; scalar-only fitting was retired.

On the corrected baseline, the doubled-rate seed-42 aware profile has 500
projections and 23,531 calendar-fit calls; full network checks and forecasts
still dominate. Inclusive profile times overlap and must not be added. Calendar
reuse addresses the remaining repeated merge work, not the cost of full-horizon
forecasting or mandatory invariants. Measurements below distinguish that limited
benefit from a claim to have eliminated the F6 bottleneck.

F6 acceptance: 262 tests pass. The 27-case matrix matches all 135 physical
artifacts and manifests; every run verifies. Eight additional pilot workloads
(seed 42 at rates ×1/×2/×4 and seed 54, both dynamic policies) match 40/40
physical files and 16/16 valid report files. The doubled-rate aware run retains
11 products, 254 events, 18 decisions, 763 candidates, 500 forecasts and exactly
2,221,400 decision-log bytes. Calendar construction falls from 23,531 merges to
171 prepared values. Five alternating, unprofiled baseline/current executions on
Python 3.12.14/Linux give medians 1.994/1.963 s and ranges 1.906–2.249/1.926–2.043 s.
The overlap does not establish a reliable whole-run speedup. Separately measured
tracemalloc peaks are 5.921/6.092 MiB; the small immutable index costs memory.
Calendar merging decreases, but F6's broader full-forecast cost remains.

## Incremental reset-route construction (F7)

`routing._reset_route_variants()` replaces the eager list of base, one-reset and
two-reset routes. It yields the base first, yields every one-reset alternative
in position/facility order, then yields two-reset descendants in the original
parent/position/facility order. Only the small one-reset frontier is retained in
a local deque; parents are released as their descendants are consumed. There
is no materialized two-reset frontier and no new pruning rule. Unlimited-range
vehicles retain their existing base-only path.

`_facility_insertions()` deep-copies each temporary route's stop/service lists.
The existing `merge_route_stops()` creates the owned route for candidate evidence,
then the same canonical key, first-duplicate retention, feasibility and scoring
code runs. Candidate IDs, rejected alternatives, all forecasts and the complete
decision log stay unchanged, including JSON field order. Old remaining routes
are still independently copied for every retained candidate. Neither the input
observation nor another candidate borrows these mutable service lists.

The unchanged pilot has unlimited range, so it does not exercise reset frontiers.
A separate controlled transport observation uses five committed stops, three
facilities, a capacity-four AV with finite range 30 (25 remaining), and five
released requests including the new request. Actual enumeration retains 7,701
candidates and exactly 13,311,995 bytes of candidate JSON in both versions. Three
unprofiled baseline/new times give medians 4.955/4.056 s, with ranges
4.844–5.149/3.637–4.125 s. A separate tracemalloc pass measures peak allocations
during enumeration at 46.149/44.178 MiB, and retained allocations at
43.353/43.316 MiB. This reduces temporary overhead, not retained complete evidence;
the total peak improvement is only about 4.3%. Serialized output is unchanged.
No RSS or serialization-buffer peak is inferred from these Python-allocation
measurements. The first baseline trial overlapped validation; reported ordinary
measurements were rerun serially without concurrent validation.

Small tests retain the original eager algorithm as an order/coverage oracle.
They compare complete JSON on the actual enumeration path, exercise coalescing
and first-duplicate retention, retain rejected candidates and verify independent
mutable routes and observations. This is not a file sink or reduced-evidence mode;
D1 remains outside scope.

F7 acceptance: 266 tests pass. All 135 matrix artifacts match the original
physical-guards baseline, and every run verifies. The eight pilot comparisons
also match F6 and the original baseline: 40/40 physical files, 16/16 valid reports
and all manifests. No ordinary artifact or candidate-evidence differences were
accepted or normalized away.

## Prefix-indexed completed processing history (F8)

Replay keeps its existing checkpoint every 100 events; no eviction scheme or new
user setting was added. Checkpoints contain current products, AVs, routes, active
jobs and queues, but their completed-processing lists remain empty.
`RunReplay._completed_operations[(plant_id, workstation_id)]` stores an immutable
tuple of `(completion_sequence, start_sequence)` pairs, once per recorded job.
The original events remain replay-owned read-only input. There is no second copy
of every historical job in each later checkpoint. Duplicate local workstation IDs
are always qualified by facility.

`snapshot()` copies a physical checkpoint and applies at most 99 records without
accumulating history. It selects visible completions by the exact event prefix,
deep-copies the corresponding original starts and applies the recorded completion
time/sequence. All history lists, windows, active jobs, last events and physical
containers returned to callers are independently mutable. No caller receives a
mutable reference to an event, index pair or checkpoint. The default public
`apply_view_event()` still accumulates full history; only internal indexed replay
uses `record_history=False`. Replay remains independent of simulation execution.

A time seek includes every record through that timestamp; an exact event prefix
can stop between a completion, inventory return and next start at the same time.
Previous/next, initial-state access, restart, interpolation and remaining operation
sequences are unchanged. Completed processing over a closed availability interval
is still displayed from the recorded events. No saved evidence is changed.

For 2,000 consecutive operations on one product (6,009 events, 61 checkpoints),
three ordinary initialization times give medians 0.580 s before and 0.077 s after.
Separately measured retained Python allocations fall 39.902→7.547 MiB; constructor
peak falls 41.263→8.606 MiB. The same nine alternating/distant prefix seeks take
45.2→48.8 ms per batch in the final measurements, a 3.6 ms increase. An earlier
repeat gave 44.7 ms after the change, so small latency differences are variable.
Lower retained memory is not a claim of universally faster seeking. Each snapshot
still copies O(visible completed jobs) history, while checkpoint physical-state
copies still grow with product/fleet size and event count. This removes the repeated
completed-history term, not all replay memory growth.

Tests compare full-history replay from the initial state with indexed replay at
checkpoint boundaries, reproducibly randomized prefixes, alternating time seeks
and same-time microstates. Snapshot mutation cannot affect subsequent snapshots.
All 93 selected complete snapshots and six Agg RGBA renders match the untouched
baseline exactly. Existing viewer tests preserve nine workstation rows, processing
overlays and callbacks. Interactive desktop/Windows rendering was not tested.

## Controlled optimization measurements

Baseline: `3be58ecafd7a9f42343fe53a4b98dcdb59074fc8`. Python 3.12.14 on the same
Linux workspace/interpreter. Resolve inputs once, change only arrival rates for
×2/×4, and use seed 42 for both policies. Seed 54 is a separate correctness case.
Each ordinary timing has three repetitions with GC before each sample. Pure
simulation excludes generation, serialization, verification and graphics. End-to-end
timing includes generation/execution/serialization in fresh folders. Verification
is timed separately on the first saved repetition. cProfile and tracemalloc run
separately from ordinary timings. No peak RSS was measured. MiB = 1,048,576 bytes;
JSON sizes below are serialized byte counts, not in-memory allocations.

Counts P/E/D/C/F mean products/events/decisions/candidates/processing forecasts.
Counts, serialized evidence and all five physical artifacts are unchanged.
Unaware policies have zero processing forecasts. Times are median [minimum–maximum]
seconds, before → final.

| Workload / seed / policy | P/E/D/C/F | Decision JSON bytes | Pure simulation before → final (s) |
| --- | --- | ---: | --- |
| pilot-x1 / 42 / unaware | 6/130/10/265/0 | 480,884 | 0.044 [0.039–0.046] → 0.039 [0.038–0.039] |
| pilot-x1 / 42 / aware | 6/150/11/173/135 | 430,950 | 0.299 [0.288–0.324] → 0.306 [0.306–0.326] |
| pilot-x2 / 42 / unaware | 11/196/15/962/0 | 2,400,470 | 0.189 [0.188–0.215] → 0.187 [0.184–0.195] |
| pilot-x2 / 42 / aware | 11/254/18/763/500 | 2,221,400 | 1.957 [1.935–1.989] → 2.007 [1.988–2.011] |
| pilot-x4 / 42 / unaware | 22/182/24/9698/0 | 56,082,672 | 7.165 [6.356–7.176] → 10.483 [9.466–14.063] |
| pilot-x4 / 42 / aware | 22/264/26/7702/1242 | 35,998,443 | 10.662 [10.484–11.908] → 10.602 [9.912–11.306] |
| seed54 / 54 / unaware | 8/187/14/211/0 | 320,175 | 0.023 [0.023–0.024] → 0.027 [0.027–0.028] |
| seed54 / 54 / aware | 8/185/14/206/142 | 527,503 | 0.304 [0.299–0.305] → 0.321 [0.320–0.322] |

| Workload / policy | End-to-end before → final (s) | Verification before → final (s) |
| --- | --- | --- |
| pilot-x1 / unaware | 0.049 [0.045–0.052] → 0.052 [0.050–0.056] | 0.106 [0.106–0.110] → 0.103 [0.100–0.105] |
| pilot-x1 / aware | 0.312 [0.295–0.321] → 0.315 [0.315–0.365] | 0.208 [0.205–0.216] → 0.207 [0.203–0.216] |
| pilot-x2 / unaware | 0.223 [0.218–0.230] → 0.226 [0.219–0.241] | 0.504 [0.492–0.510] → 0.513 [0.506–0.519] |
| pilot-x2 / aware | 2.000 [1.992–2.031] → 2.011 [1.994–2.092] | 1.015 [1.012–1.033] → 1.123 [1.057–1.131] |
| pilot-x4 / unaware | 6.557 [6.554–6.879] → 6.774 [6.737–7.259] | 14.857 [14.194–14.982] → 19.817 [14.887–25.114] |
| pilot-x4 / aware | 10.403 [9.935–10.498] → 10.778 [10.161–11.043] | 10.741 [10.294–11.457] → 10.843 [10.578–10.996] |
| seed54 / unaware | 0.034 [0.032–0.055] → 0.035 [0.031–0.035] | 0.075 [0.074–0.076] → 0.077 [0.074–0.080] |
| seed54 / aware | 0.322 [0.307–0.351] → 0.336 [0.331–0.373] | 0.225 [0.221–0.227] → 0.243 [0.230–0.245] |

The large unpaired ×4 unaware timings were noisy, including an apparent slowdown.
Investigation used three alternating fresh-process baseline/final pairs: medians
6.533/5.974 s, ranges 5.881–7.759/5.860–5.987 s. This did not reproduce the unpaired
slowdown. The end-to-end medians were also similar, 6.557/6.774 s. These observations
do not establish a robust pilot runtime improvement or an algorithmic regression.
The pilot has unlimited range and does not exercise F7 reset construction. Keep
the raw range information in view; do not attribute unrelated timing noise to F7.

| Allocation scope | Peak Python MiB before → after | Retained Python MiB before → after | Serialized evidence |
| --- | ---: | ---: | --- |
| ×2 unaware simulation, seed 42 | 7.180 → 7.187 | Not measured | 2,400,470 decision bytes, unchanged |
| ×2 aware simulation, seed 42 | 5.921 → 6.092 | Not measured | 2,221,400 decision bytes, unchanged |
| Finite-route enumeration, 7,701 candidates | 46.149 → 44.178 | 43.353 → 43.316 | 13,311,995 candidate-array bytes, unchanged |
| Replay initialization, 2,000 jobs / 6,009 events | 41.263 → 8.606 | 39.902 → 7.547 | Same saved run |

Tracemalloc scopes start immediately before the measured call. Enumeration keeps
its complete returned candidates; existing input/previously serialized JSON is
not counted as new allocations. Replay retained measurements retain the RunReplay
instance, not caller snapshots. Profiling inclusive times overlap and are not
summed. On ×2 aware, fitting is called 23,531 times in both versions; the original
fitting self time was 0.086 profiled seconds and prepared fitting 0.026, with
0.016 self seconds in calendar preparation. Whole forecasts, copies and mandatory
conservation checks remain the dominant cost. F7's complete retained decision
history still dominates its measured peak. F8's snapshot-history copying remains
linear in visible completed work. D1 was not implemented.

### Local before/after check

After running the README's seed-42 example, use its saved resolved inputs and
results in both worktrees. Reuse the same absolute `$thesisPython`. This snippet
prints three-sample medians/ranges and measures allocations in a separate run.
It writes no evidence. Use a saved run with more completed operations when checking
F8; a tiny pilot has little duplicated history to remove. The controlled long case
above historically used the legacy fixture with coincident CP/RF, release/departure zero,
`operations=[1]*2000`, WS1 duration 1/window `[0,2001]`, and horizon 2001.

```powershell
$currentTree = (Get-Location).Path
$baselineTree = Join-Path (Split-Path $currentTree -Parent) ("thesis-perf-baseline-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$resolved = (Resolve-Path (Join-Path $aware42 'scenario.json')).Path
$savedRun = (Resolve-Path $aware42).Path
git fetch origin refs/heads/simulation-v8-fix-physical-guards:refs/remotes/origin/simulation-v8-fix-physical-guards
if ($LASTEXITCODE -ne 0) { throw 'Baseline fetch failed' }
git worktree add --detach $baselineTree 3be58ecafd7a9f42343fe53a4b98dcdb59074fc8
if ($LASTEXITCODE -ne 0) { throw 'Baseline worktree creation failed' }

$probe = @'
import gc, hashlib, json, statistics, sys, time, tracemalloc
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from simulation.config import as_dynamic_scenario
from simulation.engine import run_dynamic_simulation
from tools.replay import RunReplay
scenario = as_dynamic_scenario(json.loads(Path(sys.argv[1]).read_bytes()))
scenario['policy'] = 'dynamic_quality_aware'
def samples(call):
    values = []
    for _ in range(3):
        gc.collect()
        start = time.perf_counter()
        result = call()
        values.append(time.perf_counter() - start)
        del result
    return {'median_s': statistics.median(values), 'range_s': [min(values), max(values)]}
print('simulation', samples(lambda: run_dynamic_simulation(scenario)))
gc.collect(); tracemalloc.start()
state = run_dynamic_simulation(scenario)
print('simulation_peak_python_MiB', tracemalloc.get_traced_memory()[1] / 2**20)
tracemalloc.stop()
print('event_decision_sha256', hashlib.sha256(json.dumps([state.events, state.decisions]).encode()).hexdigest())
del state
print('replay_initialization', samples(lambda: RunReplay(Path(sys.argv[2]))))
gc.collect(); tracemalloc.start()
replay = RunReplay(Path(sys.argv[2]))
retained, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
print('replay_python_MiB', {'retained': retained / 2**20, 'peak': peak / 2**20})
prefixes = [0, len(replay.events), 1, len(replay.events)//2, len(replay.events)-1]
def seek_batch():
    return [replay.snapshot(replay.times[n-1] if n else 0, n) for n in prefixes]
print('five_prefix_seeks', samples(seek_batch))
'@
foreach ($tree in @($baselineTree, $currentTree)) {
    Push-Location $tree
    try {
        Write-Host $tree
        $probe | & $thesisPython - $resolved $savedRun
        if ($LASTEXITCODE -ne 0) { throw 'Performance probe failed' }
    } finally { Pop-Location }
}
```

For ×2/×4 workloads, copy the current pilot configuration under ignored `results/`,
multiply only each `collection_points[].generation.arrival_rate` by 2 or 4, resolve
seed 42 once and use that saved `scenario.json` in both worktrees. Keep coordinates,
calendars, quality, horizon and fleet declarations unchanged. Ordinary timings,
tracemalloc and cProfile should be separate passes with no competing validation
process. Repeated alternating baseline/current subprocesses help identify noisy
measurements; do not expect these machine-specific seconds on Windows.

### Integrated acceptance

The untouched baseline passes 256 tests. F6/F7/F8 pass 262/266/272 tests,
respectively. Each stage's 27-case matrix matches all 135 physical files and 54
valid verification report files byte-for-byte against the original physical-guards
baseline; manifests match with run IDs explicitly checked against their own output
folders. All comparison runs pass independent verification. The eight measured
pilot cases additionally match 40/40 physical files, 16/16 reports and eight
manifests. No difference was normalized away and no historical fixture was changed.

The final seed-42–61 batch completes and independently verifies 40/40 dynamic
runs with matching saved inputs within each seed. Repeats of seed 42 and 54 in
reversed policy order match all 20 physical files. Two supported format-two
experiments each complete eight verified runs, with identical physical outputs
under reversed policy order. KPI export yields 40 valid seed rows with zero skips;
preferred, compatibility and standalone CSVs match exactly. Standalone verifier,
compatibility commands, headless execution with plotting imports blocked and
both then-available viewer entry points under Agg passed. This is historical
performance-memory acceptance: the simulation.visualization alias was subsequently
retired; current commands use tools.viewer or simulation --visualize.
The ten delivery tests, eight physical
guard tests and twenty numerical tests also pass with optimization enabled.
F1–F5 coverage remains in the complete suite; independent verifier and KPI source
files are unchanged. Current pilot, historical input and fixture bytes are unchanged.
No simulation features, policy search changes, reduced evidence or D1 failure-output
mechanism was introduced. Original branches and the uncommitted audit report remain
available; benchmark runs, JSON/CSV evidence, profiles and images stay outside Git.
