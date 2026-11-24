# Checkpoint 4c: dynamic quality-aware routing

Branch `thesis-checkpoint-4c` starts from `thesis-checkpoint-4b`, commit
`1f9977a6d8e22c85d7c55a4cd395ae4a407bb5cb`. Source, tests and documentation are maintained on `thesis-checkpoint-4c` for review.
Sections 6, 7 and 12 of the supplied specification informed this implementation;
no subsequent checkpoint, experiment framework or visualization is included.

## Observed comparison and decision

Both policies read the exact same bytes of `configs/checkpoint_4c.json`. Only the
execution policy in the manifest differs. The source uses `plants` for RF1/RF2;
the execution copy adds a primary `plant` alias for backward compatibility.
Station IDs are local to a plant: both plants can have a WS1.

| Measurement | Unaware expected / observed | Aware expected / observed |
|---|---|---|
| Destination | RF1 / RF1 | RF2 / RF2 |
| Pickup | 1 / 1 | 1 / 1 |
| Travel distance | 2 / 2 | 4 / 4 |
| Admission | 2 / 2 | 4 / 4 |
| First processing start | 8 / 8 | 4 / 4 |
| Inventory wait | 6 / 6 | 0 / 0 |
| Completion | 9 / 9 | 5 / 5 |
| Independent verification | PASS, 0 / PASS, 0 | PASS, 0 / PASS, 0 |

Both finish with one remanufactured product, zero unfinished products, zero
recycled products and zero waiting-to-unload time. Both use the same horizon 10,
speed 1, unlimited-range AV, release at zero and one-unit operation. Paths differ
because dynamic routing chooses the destination.

At time zero, `main()` parses the source, policy and output path. `run_once()`
validates a copied execution scenario, preserving source bytes, then invokes
`run_dynamic_simulation()`. The loop releases P1, batches the release and initial
AV availability, and calls `dispatch_dynamic_requests()`.
`build_dynamic_policy_input()` supplies actual position `(0,0)`, empty payload,
no commitments and the released P1. The unaware input excludes operations and
plant processing; the aware input adds P1's operations and a copied physical
context. Neither input contains an individual quality class. Future returns are
excluded from the context, not merely ignored during ranking.

`choose_dynamic_insertions()` reuses 4b's complete insertion enumeration and
capacity/range/precedence checks. Two feasible choices are CP1→RF1 and CP1→RF2.
For each aware candidate it calls `forecast_dynamic_insertion()`:

```
forecast_dynamic_insertion(policy_input, RF1_candidate)
  arrival=2, admission=2, first_start=8, inventory_wait=6
forecast_dynamic_insertion(policy_input, RF2_candidate)
  arrival=4, admission=4, first_start=4, inventory_wait=0
```

These are isolated physical projections from the decision time through horizon
10, with dispatch disabled, the proposed route replacing the previous route,
and all already committed deliveries retained. They use current inventory,
intermediate-operation holds, active operations, calendars, waiting AV priority
and all revised routes. Completion/admission/start ordering uses the same plant
physics as 4a. Transport is projected too: an earlier blocked unloading can delay
a later delivery. No copied projection mutates the live run.

The aware score is `[horizon_flag, admission_to_start_wait, added_distance,
vehicle_id, destination_id, pickup_index, delivery_index, extra_visit_count,
canonical_route]`. The flag is zero for a first start at/before the horizon and
one otherwise. With no horizon start the wait component is zero, leaving added
distance as the ranking criterion. Distances retain 4b's 12-decimal ranking
rounding. Remaining ties retain the stable 4b ordering. RF1 starts with
`[0,6,2]`; RF2 starts with `[0,0,4]`. RF2 wins on wait. The unaware score starts
with distance, so RF1 wins with 2 rather than 4.

`dispatch_dynamic_requests()` logs the decision and applies assignments/routes.
The AV departs at zero, picks up P1 at CP1 at one, and reaches RF2 at four.
Facility arrival resets finite range if configured, queues service at RF2,
admits P1 and starts its final operation at the same timestamp. No slot is held
for that final operation. It completes at five. The loop stops at ten and the
runner writes final state and summary.

Inspect the **first line** of `dispatch_decisions.jsonl`:

- `policy_input`: the actual decision-time transport/plant observation;
- `requests[0].candidates`: all retained feasible/infeasible insertions;
- each feasible aware candidate: `predicted_arrival`, `predicted_admission`,
  `predicted_first_start`, `predicted_inventory_wait`, `projected_deliveries`, `score`;
- `requests[0].selected_candidate_id` and `requests[0].reason`;
- `proposals[0].destination`: `RF2` for aware, `RF1` for unaware.

The aware reason is
`start_within_horizon_then_admission_wait_then_added_distance_then_stable_ties`.
Revised committed delivery ETAs are in `projected_deliveries`, not taken from a
superseded active leg's scheduled arrival. Infeasible routes are not projected.

## Independent verification and tests

The standalone standard-library checker never imports `simulation`. It rebuilds
physical state from saved evidence, reconstructs the released-only policy input,
enumerates candidates itself and performs its own chronological route/plant
sweep. That sweep implements holds, active work, calendars, unloading priorities,
revised geometry and blocked-route continuation. It compares every prediction,
score, chosen ID/reason and route, then reconciles events, final state and totals.
The scenario hash still checks **raw bytes**, with no normalization.

In a copied aware run, changing only RF2 candidate `predicted_first_start` to 9:

| Check | Expected | Observed |
|---|---|---|
| Valid unaware | PASS / exit 0 | PASS / exit 0 |
| Valid aware | PASS / exit 0 | PASS / exit 0 |
| Forged start | FAIL / exit 1 | FAIL / exit 1 |
| Violation | predicted start 4 vs 9 | `dynamic.forecast.predicted_first_start`, expected 4, observed 9 |
| Location | P1, AV1, RF2, time 0 | P1, AV1, RF2, time 0, event sequence 3 |
| Existing tests | 90 pass | 90 pass |
| Full suite | 104 pass | 104 pass (90 existing + 14 new) |

Additional concrete forecast tests:

- At time 5 of the 4b reroute, P1's old ETA 8 becomes
  `5 + sqrt(2) + sqrt(5) = 8.650281539872886`; P2 first starts at
  `9.650281539872886` behind P1, and the verifier passes the full run.
- At time 3, RF1's only slot is held by P0's intermediate operation until its
  final operation starts at 8. Candidate P1 arrives at 5 but admission is 8,
  first start 9. Projection leaves live state and its input unchanged.
- A committed earlier RF1 unloading blocks until 8; a later RF2 delivery is
  projected at 12, accounting for travel after actual unloading. Both independent
  forecast and simulation projection agree; a geometric ETA alone would be 8.

Search remains the 4b heuristic: all insertion positions with up to two extra
facility reset visits for range-limited AVs. Forecasts are horizon-bounded and
conditioned on known commitments, not assumptions about future returns or
future dispatches. Each configured plant must support all resolved
remanufacturing operation lists; heterogeneous plant eligibility is not added.
This keeps unaware routing free of individual operation compatibility inputs.

## Exact PowerShell reproduction

Run in the repository in VS Code. Use fresh output directories. Keep your existing
`$thesisPython` assignment, or set it to the local environment interpreter:

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $thesisPython -m unittest discover -s tests -v
# Expected: Ran 104 tests ... OK
& $thesisPython -m unittest discover -s tests -p test_dynamic_quality.py -v
# Expected: Ran 14 tests ... OK

$runRoot = Join-Path (Get-Location) 'results\checkpoint-4c-review'
& $thesisPython -m simulation --resolved-scenario configs/checkpoint_4c.json --policy dynamic_quality_unaware --output "$runRoot\unaware"
& $thesisPython -m simulation --resolved-scenario configs/checkpoint_4c.json --policy dynamic_quality_aware --output "$runRoot\aware"
& $thesisPython verify_simulation.py "$runRoot\unaware"
$LASTEXITCODE # 0
& $thesisPython verify_simulation.py "$runRoot\aware"
$LASTEXITCODE # 0
(Get-FileHash "$runRoot\unaware\scenario.json").Hash -eq (Get-FileHash "$runRoot\aware\scenario.json").Hash
# True

Get-Content "$runRoot\unaware\summary.json"
Get-Content "$runRoot\aware\summary.json"
$decisions = @(Get-Content "$runRoot\aware\dispatch_decisions.jsonl" | ForEach-Object { $_ | ConvertFrom-Json })
$decisions[0].requests[0].candidates | Format-List candidate_id,destination,predicted_arrival,predicted_admission,predicted_first_start,predicted_inventory_wait,added_distance,score,projected_deliveries
$decisions[0].requests[0] | Format-List selected_candidate_id,reason
$decisions[0].proposals[0].destination # RF2

Copy-Item "$runRoot\aware" "$runRoot\forged" -Recurse
$bad = @(Get-Content "$runRoot\forged\dispatch_decisions.jsonl" | ForEach-Object { $_ | ConvertFrom-Json })
$rf2 = $bad[0].requests[0].candidates | Where-Object { $_.destination -eq 'RF2' -and $_.feasible } | Select-Object -First 1
$rf2.predicted_first_start = 9
$lines = @($bad | ForEach-Object { $_ | ConvertTo-Json -Depth 100 -Compress })
$logPath = Join-Path $runRoot 'forged\dispatch_decisions.jsonl'
[System.IO.File]::WriteAllText($logPath, ($lines -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
& $thesisPython verify_simulation.py "$runRoot\forged"
$LASTEXITCODE # 1
Get-Content "$runRoot\forged\verification.txt"

git check-attr text eol -- tests/fixtures/checkpoint_4c/scenario.json
# text: set; eol: lf
```

These commands have Python equivalents executed here; PowerShell itself was not
available in this Linux environment. Generated result directories and `.venv`
remain ignored. The new frozen six-file evidence fixture is intentional test data;
its saved scenario is LF-pinned in `.gitattributes`.

## Every added or changed function

The following contracts name callers, input/output and side effects. `Record`
means a typed dictionary alias for JSON-like data. Functions retain type hints
and short docstrings. Unless stated otherwise, they perform no file I/O.

### Entry, configuration and state

| Function | Caller; receives → returns | State/files changed |
|---|---|---|
| `simulation.__main__.main()` | `python -m simulation`; CLI arguments → exit integer | Delegates run writing to runner; prints summary/errors. Adds aware dynamic policy choice. |
| `runner.run_once()` | CLI/tests; source Path, new output Path, optional seed/policy → summary dictionary | Reads source, creates run directory and six evidence files; execution copy selects either dynamic policy. Empty plant map omitted from legacy export. |
| `dynamic_config.as_dynamic_scenario()` | Runner/tests; resolved scenario → copied dynamic scenario | Adds primary alias and preserves either dynamic policy; original untouched. |
| `dynamic_config.validate_dynamic_scenario()` | Runner; execution scenario → None or exception | Validates identifiers and every plant's physical limits/operation support; no mutation. |
| `models.build_inventory_state()` | State builders; scenario → new InventoryRunState | Initializes positions using any configured facility; no input mutation. `InventoryRunState.plant_states` is a new default-empty per-plant container field. |
| `forecasting.build_forecast_context()` | Existing static-aware boundary; scenario/live state → copied forecast context | Omits empty new field to preserve legacy static boundary/fixtures. Live state unchanged. |
| `vehicle_range.network_site()` | Transport/range helpers; scenario/site ID → definition | Includes every configured plant; read only. |
| `vehicle_range.evaluate_fixed_trip_range()` | Fixed-trip dispatch/range check; scenario/state/trip/start index → feasibility evidence | Facility list includes every plant; no live changes. |
| `vehicle_range.reset_vehicle_range()` | Facility-arrival handlers; scenario/state/AV ID → None | Sets finite allowance to maximum, records reset event; accepts any plant as facility. Unlimited AV emits no reset. |

### Shared plant network

| Function in `plant_network.py` | Caller; receives → returns | State/files changed |
|---|---|---|
| `configured_plants()` | Config, routing, engine, range-related callers and forecasts; scenario → plant list | Read only; legacy single plant becomes one-element list. |
| `initialize_plant_states()` | Dynamic state builder and projection; scenario/state → None | Creates distinct plant maps once, rebinds primary aliases to authoritative containers after deepcopy/asdict reconstruction. |
| `local_plant_view()` | Network settlement and stop/completion handlers; scenario/state/plant ID → local scenario and state view | View references selected inventory/holds/stations/queue and global products/vehicles/events; creating it does not transition products. |
| `settle_plant_network()` | Dynamic loop/projection; scenario/state → scheduled completion tuples | Runs existing admission/start rules per plant in stable ID order, updating live local containers/products/events. Tags completions with plant and workstation IDs. |
| `check_plant_network()` | Dynamic loop/projection; scenario/state → None or assertion | Checks capacities, payload and conservation across authoritative maps once; read only. |

For example, `local_plant_view(scenario, state, 'RF2')` at time 4 exposes RF2's
WS1, not RF1's identically named WS1. `settle_plant_network()` admits P1 there,
starts it at 4, and schedules `(5, 0, 'RF2', 'complete', 'WS1')`.

### Dynamic routing, projection and execution

| Function | Caller; receives → returns | State/files changed |
|---|---|---|
| `dynamic_routing.build_dynamic_policy_input()` | Dispatch; scenario/live state → boundary dictionary | Copies all site geometry and released transport requests; aware-only context/operations; does not change live state. |
| `dynamic_routing.choose_dynamic_insertions()` | Dispatch; boundary → proposals and per-request audits | Mutates only a copied working boundary to retain prior selections in this batch. Projects feasible aware candidates, ranks and records reasons. |
| `dynamic_forecasting.build_dynamic_forecast_context()` | Aware boundary builder; scenario/state → copied physical scenario/state | Filters unreleased products and drops logs/quality classes; no live changes. |
| `dynamic_forecasting.forecast_dynamic_insertion()` | Candidate selector; boundary/candidate → predicted times, wait and all projected deliveries | Reconstructs isolated state, applies revised routes/assignments, runs dispatch-disabled physics. Only copied state and local observer list change. |
| `dynamic_forecasting.dynamic_quality_score()` | Candidate selector; enriched candidate → score list | Pure; RF2 produces prefix `[0,0,4]`. |
| `dynamic_engine.build_dynamic_state()` | Dynamic run; scenario → new live state | Initializes all facility positions and plant maps. |
| `dynamic_engine.start_dynamic_leg()` | Dynamic loop/projection; scenario/state/AV ID → tagged arrival tuple or None | Starts head leg, increments leg identity, writes departure event in state; includes every plant in lookup. |
| `dynamic_engine.service_dynamic_stop()` | Arrival handler; scenario/state/AV ID → None | Picks up assigned products or queues deliveries in destination-local plant state/recycler; appends physical events. |
| `dynamic_engine.finish_dynamic_stops()` | Loop; scenario/state → newly available AV IDs | Removes fully served head stops; recognizes every plant as facility. Keeps unfinished unload commitments. |
| `dynamic_engine.dispatch_dynamic_requests()` | Loop at batched trigger; scenario/state/trigger → None | Logs boundary/candidates/proposals; applies assignments and route changes, invalidating superseded legs. Its docstring now covers either policy. |
| `dynamic_engine.run_dynamic_simulation()` | Runner or forecaster; scenario, optional initial state, dispatch flag, delivery observer → final state | Advances motion/events/plant work, settles network, finishes waits and summary. Projection mode suppresses future releases/dispatch and mutates only supplied copied state; observer receives actual delivery arrival records. Normal mode runs live releases/replans. No file I/O. |

For the ETA regression, `forecast_dynamic_insertion(boundary_at_5, candidate)`
replaces P1's old RF1 arrival 8 with the revised route through CP2. Its
`projected_deliveries` reports P1 and P2 at 8.650281539872886. Workstation ordering
then puts P2's first start at 9.650281539872886.

### Independent verifier (all in `verify_simulation.py`)

These functions use verifier-owned dictionaries and no simulation imports.

| Function/method | Caller; receives → returns | State/files changed |
|---|---|---|
| `audit_configured_plants()` | Validation/replay/forecast; scenario → plant list | Read only; own legacy fallback. |
| `audit_plant_containers()` | Own forecast/context; scenario/replay state → authoritative plant map | Read only; avoids double counting primary aliases. |
| `audit_dynamic_forecast_context()` | `DynamicAudit.policy_input`; scenario/replay state → released-only copied context | Copies own reconstructed state; no physical mutation. |
| `audit_dynamic_forecast()` | Own candidate selection/tests; boundary/candidate → predictions and projected deliveries | Independent chronological dictionary sweep on copied state; handles all revised routes, queues, holds and calendars. No file I/O/live mutation. |
| `audit_dynamic_quality_records()` | Reconcile; expected/observed decision, findings list, dispatch event → None | Appends precise forecast/score violations including time, event sequence and IDs. |
| `audit_dynamic_shape()` | `verify_run`; saved scenario → copied execution scenario | Adds primary alias and preserves selected dynamic policy; saved bytes untouched. |
| `validate_dynamic_inputs()` | `verify_run`; scenario → None or exception | Independently checks all plants, sites, origins and physical validity; read only. |
| `audit_dynamic_selection()` | `prepare_dispatch`; reconstructed boundary → proposals/audits | Enumerates and scores independently, updates only working copy commitments. |
| `DynamicAudit.__init__()` | `verify_run`; scenario/shared findings → initialized instance | Creates independent per-plant state, actual vehicle state and pending notices/proposals. |
| `DynamicAudit.consume_plant_event()` | `consume_event`; event/plant ID → None | Temporarily binds local plant containers/station definitions, applies own transition checks, restores primary bindings in finally block. |
| `DynamicAudit.check_physical_state()` | Replay/transition checks; event → None | Reads global membership and each capacity/hold set; appends violations. |
| `DynamicAudit.policy_input()` | Dispatch audit; current instance → boundary | Rebuilds transport facts plus aware-only physical context; physical state unchanged. |
| `DynamicAudit.pending_deadlines()` | Replay; current instance → required times list | Includes every plant's active work/admission/start, plus transport/dispatch deadlines; read only. |
| `DynamicAudit.prepare_dispatch()` | Dispatch-event handler; event → None | Checks same-time settlement, computes independent expected selections and pending atomic events; findings on mismatch. |
| `DynamicAudit.consume_event()` | Replay (and local plant delegate); event → None | Validates/applies transport or destination-local physical transition; independently aggregates horizon counts and appends findings. |
| `DynamicAudit.replay()` | `verify_run`; event list → independently reconstructed final dictionary | Advances instance state, checks events/deadlines, finalizes all plants' wait totals. |
| `DynamicAudit.reconcile()` | `verify_run`; evidence dictionary → calculated summary | Checks predictions, proposals, complete saved final state and totals; appends findings. |
| `verify_run()` | Existing standalone CLI/tests; run-folder Path → report dictionary | Reads evidence, hashes raw scenario bytes, reconstructs appropriate policy and writes verification.json/txt. Supports aware dynamic policy; existing 0/1/2 meanings preserved. |

Concrete audit: `audit_dynamic_forecast(boundary_at_0, RF2_candidate)` returns
first start 4. When `audit_dynamic_quality_records()` sees recorded 9, it appends
`dynamic.forecast.predicted_first_start`, expected 4, observed 9, time 0,
event sequence 3, IDs P1/AV1/RF2. This finding is independent of the saved score.

### Every test/helper in `tests/test_dynamic_quality.py`

`unittest` calls `setUp()` before each test and calls each `test_*()` without
arguments. All tests return None and assert behavior; no repository source or
frozen fixture is rewritten. Temporary files are cleaned by registered cleanup.

| Method | Receives/returns; callers and effects |
|---|---|
| `setUp()` | unittest → None; allocates TemporaryDirectory, scenario copy, counter, cleanup. |
| `run_policy()` | Tests; optional scenario/policy → folder, decisions, final state; writes temp source and run evidence, increments counter. |
| `verify_policy()` | Tests; folder/expected exit → report; subprocesses independent checker, writes only temporary verification reports and asserts status. |
| `save_decisions()` | Forgery test; folder/decisions → None; rewrites temporary JSONL with explicit UTF-8/LF. |
| `test_requested_two_plant_comparison_same_inputs()` | Uses run/verify helpers; asserts identical bytes/hashes, RF1 versus RF2 choices, every requested time/wait/distance and scores. Writes two temp runs/reports. |
| `test_forged_predicted_start_is_rejected()` | Uses run/save/verify; changes RF2 start 4→9 in temp log, checks exact rule/IDs/event/values and exit 1. |
| `test_revised_existing_delivery_eta_changes_forecast()` | Uses 4b config with aware policy; verifies revised P1 ETA, P2 start and full valid run. Temp evidence only. |
| `test_held_slot_delays_admission_and_projection_is_immutable()` | Constructs copied live state at 3 with P0 held slot; invokes boundary/selection and own audit directly, asserts arrival 5/admission 8/start 9 and unchanged original state/context. No run files. |
| `test_horizon_fallback_distance_and_stable_destination_ties()` | Runs calendar/tie variants; asserts horizon-start preference, distance fallback and stable destination IDs. Temp runs/reports. |
| `test_future_returns_and_unaware_visibility()` | Runs both policies with later P2; inspects initial released-only boundary, absence of unaware operations/context and valid reports. |
| `test_nonprimary_stock_and_processing_at_horizon()` | Runs short-horizon variants; checks RF2 active work and stock/counts plus verifier reconciliation. Temp runs/reports. |
| `test_all_operations_stay_at_the_chosen_plant_with_held_capacity()` | Runs two-operation finite-capacity case; checks both RF2 history entries and held-slot behavior, verifies run. |
| `test_mixed_recycling_keeps_prior_destination_commitments()` | Adds recyclable P2 and capacity 2; checks P1 destination remains RF2 after another insertion, verifies run. |
| `test_range_infeasible_candidates_are_not_projected()` | Runs maximum range 1; checks rejection/no prediction/no travel, verifies valid unserved run. |
| `test_seeded_single_plant_aware_replay()` | Resolves existing seeded config, runs/replays aware; checks same saved bytes/events and verifier passes. Temp configs/runs. |
| `test_cli_policy_pair_and_default_aware_policy()` | Executes actual CLI for both overrides and aware source default; checks destination, reports and exit codes. Temp sources/runs. |
| `test_frozen_aware_fixture_hash_lf_and_isolated_verifier()` | Reads frozen fixture bytes/hash, copies checker alone with evidence into temp isolated directory, checks PASS and LF; original unchanged. |
| `test_wait_at_earlier_plant_propagates_to_later_delivery_eta()` | Constructs time-3 committed route/held slot directly; candidate arrives RF1 geometrically at 4 but unloads at 8 and reaches RF2 at 12. Calls selector and independent forecast, checks both report 12. Only local copied state changes. |

## Changed files

- `.gitattributes`: LF rule for new hashed scenario fixture.
- `configs/checkpoint_4c.json`: shared deterministic two-plant input.
- `simulation/__main__.py`, `runner.py`, `dynamic_config.py`: policy and multi-plant input support.
- `simulation/models.py`, `forecasting.py`, `vehicle_range.py`: network state/range lookup and legacy compatibility.
- `simulation/plant_network.py`, `dynamic_forecasting.py`: plant-local views and isolated candidate projections.
- `simulation/dynamic_engine.py`, `dynamic_routing.py`: network execution and aware insertion ranking/logging.
- `verify_simulation.py`: independent network replay, forecast reconstruction and decision checks.
- `tests/test_dynamic_quality.py`: fourteen tests and four helpers.
- `tests/fixtures/checkpoint_4c/{scenario.json,run_manifest.json,events.jsonl,dispatch_decisions.jsonl,final_state.json,summary.json}`: frozen valid aware evidence.
- `README.md` and this document: review/reproduction guide.

Generated demonstrations live under ignored `results/checkpoint-4c-review`;
verification/test scratch output lives under ignored `work`. No generated results
or environment files are added to source control. Stopped after checkpoint 4c.
