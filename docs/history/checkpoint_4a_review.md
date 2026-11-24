# Checkpoint 4a — static quality-aware selection

Created `thesis-checkpoint-4a` from `thesis-checkpoint-3c` at
`6b9e0077bf037086cd75c605785c536b3951f36a`. Only checkpoint 4a is implemented.
Source, tests and documentation are maintained on `thesis-checkpoint-4a`.

## Fixed inputs and execution policy

Use the SAME `configs/checkpoint_4a.json` with either
`--policy static_quality_unaware` or `--policy static_quality_aware`.
The saved scenario bytes and SHA-256 are identical. The selected execution
policy is recorded in `run_manifest.json`; `scenario.policy` is the default
when no override is supplied. The runner changes only an in-memory execution
copy, never the saved resolved inputs. Seed overrides remain prohibited for
resolved replay. To replay an aware run, supply its manifest's aware policy
explicitly if the saved scenario default is unaware.

Version two supports both static policies. Version one remains the legacy
single-product unaware path. Unknown policies are rejected before creating an
output directory. Existing seeded generation and range/plant/vehicle rules are
shared unchanged. There are no dynamic insertions, changed stops/departures,
new destination choices, experiments or visualization.

The prior reservation timing is retained: static product selection occurs at
the configured departure opportunity, using only requests released by that time.
There is no reservation of future returns. This fixture releases both products
before departure, as requested.

## Example and actual results

RF1 `(0,0)`, CP1 `(1,0)`, AV1 speed 1/payload 1, departure 1, fixed stops
CP1/RF1. Horizon 10; inventory/range unlimited. P1 releases at 0 with route
`[1]`; P2 releases at 0.5 with route `[2]`. Both machines take one unit. WS1
is open `[8,10)` and WS2 `[3,10)`.

| Quantity | Expected unaware | Observed unaware | Expected aware | Observed aware |
| --- | --- | --- | --- | --- |
| Selected | P1 | P1 | P2 | P2 |
| Departure / pickup / admission | 1 / 2 / 3 | 1 / 2 / 3 | 1 / 2 / 3 | 1 / 2 / 3 |
| First processing start | 8 | 8 | 3 | 3 |
| Completion | 9 | 9 | 4 | 4 |
| Plant inventory wait | 5 | 5 | 0 | 0 |
| Travel distance | 2 | 2 | 2 | 2 |
| Generated / remanufactured / unfinished | 2 / 1 / 1 | 2 / 1 / 1 | 2 / 1 / 1 | 2 / 1 / 1 |
| Independent verification | PASS / 0 | PASS / 0 | PASS / 0 | PASS / 0 |

The unselected product stays at CP1 in each run. Both travel traces have the
same origin, target, time, speed and distance; only their carried product differs.
Both save exactly the same input bytes/hash, vehicle schedule and destination.

At decision time 1, after release records 1 and 2, aware candidate evidence is:

| Candidate | Arrival | Admission | First start | Admission-to-start wait | Added distance | Score |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | 3 | 3 | 8 | 5 | 0 | `[0,5,0,"P1",0,1]` |
| P2 | 3 | 3 | 3 | 0 | 0 | `[0,0,0,"P2",0,1]` |

Score fields are: no-start-within-horizon flag, admission-to-first-start wait
(or zero placeholder in fallback), added distance, product ID, pickup stop
index, delivery stop index. Prefer a first start at or before the horizon; then
less admission-to-start wait; then less added distance; then stable IDs/stops.
If no start is forecast, use fallback flag 1 and compare distance then IDs/stops.
Added distance is zero for every candidate because this policy cannot change
the fixed trip. A missing admission/start is represented by null, never infinity.

Candidate enumeration uses release/ID order; the winning score's stable tie is
product ID, rather than release time. For larger payloads, fill one slot at a
time: each round projects prior selected products plus each remaining candidate.
Forecasts are explicitly conditional on that round's load, not a globally
optimized set or a promise that later load additions cannot change its timing.
All candidates/rounds, selected flags and the comparison reason are logged.

Recycling trips filter by disposition as before. Recycling acceptance has a
predicted admission/acceptance time but no processing start; those candidates
use the stable fallback tie. No recycling part is presented as workstation work.

## Forecast inputs and physical projection

The unaware boundary remains unchanged: released ID, origin, release time,
disposition, fixed stops and vehicle ID. It receives no individual operations,
quality class, plant forecast context or quality-derived ranking fields.

The aware boundary adds required operations and an explicit `forecast_context`:
only released product definitions/state, current stock and holds, active work,
vehicle state/committed assignments, fixed calendars/network/trips, horizon and
counts of already departed legs. Future unreleased product IDs, attributes and
random arrivals are excluded. Quality class IDs are unnecessary and omitted.
The selector consumes this recorded input object, not the original live state.

Each candidate projection deep-copies that context, adds the candidate load,
prior round selections and already committed plant deliveries at fixed-route
arrival times. Existing waiting AVs retain original queue priority. It advances
calendar openings, active-operation completions and known deliveries, then
retries admissions and workstation starts under the SAME physical plant/helper
rules as the engine. It executes all required steps of known in-plant parts,
including hold replacement and final-slot release, until the candidate starts
or the horizon is reached. It never changes live state or books forecast slots
in the live plant.

Tests cover a committed P0 arriving at 2, starting intermediate WS1 at 2–4,
holding the only inventory slot, returning at 4, and waiting until final WS2
start 6 frees the slot. A candidate arriving at 3 cannot be admitted until 6.
P1 then starts at 8, P2 at 7 because P0 uses WS2 at 6–7. The same forecast is
checked when departure happens while P0 is still travelling or already processing.
Another case keeps P0's hold past the horizon; both candidates have null
admission/start and use fallback instead of a false idle-machine opportunity.

The standalone verifier uses its own dictionary-based chronological projection;
it does not import simulation/forecast/plant code. It reconstructs the expected
boundary snapshot, every candidate forecast/score, coverage, reason and choice
from actual replay state, then compares the observed decision log. A missing
manifest cannot be used to guess an execution-policy override: verification is
incomplete. Physical event replay and final-state checks remain independent.

## Forged decision demonstrations

A copy of the aware dispatch log changes P1's wait score to -1:

```text
FAIL / exit 1
rule: policy.candidate.score
time: 1
associated event sequence: 3 (assignment following the decision)
product: P1, vehicle: AV1, selection round: 0
expected: [0,5,0,"P1",0,1]
observed: [0,-1,0,"P1",0,1]
```

A separate copy changes assigned_products to P1:

```text
FAIL / exit 1
rule: policy.selection
expected: ["P2"]
observed: ["P1"]
time: 1, associated event sequence: 3
```

Neither copy changes scenario bytes, vehicle events, summary or final state.
The reports identify a policy-audit violation even though physical replay is
still complete. Original runs and records are unchanged.

All existing 59 tests and 15 new tests pass: 74 total. No old test was edited.
The new saved aware fixture pins its hashed scenario to LF, preserving the
existing byte-for-byte fingerprint convention.

## Exact PowerShell commands

From the repository root with these changes present and the existing venv.
Python commands and equivalent artifact mutations ran in the cloud; PowerShell
itself is unavailable there. Syntax targets Windows PowerShell 5.1/7.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path

# Existing 59 tests:
& $thesisPython -m unittest discover -s tests -p test_checkpoint_1.py -v
& $thesisPython -m unittest discover -s tests -p test_verifier.py -v
& $thesisPython -m unittest discover -s tests -p test_inventory.py -v
& $thesisPython -m unittest discover -s tests -p test_return_generation.py -v
& $thesisPython -m unittest discover -s tests -p test_vehicle_range.py -v
# New 15, then all 74:
& $thesisPython -m unittest discover -s tests -p test_static_quality.py -v
& $thesisPython -m unittest discover -s tests -v

$demoRoot = Join-Path .\results ("checkpoint-4a-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$unaware = Join-Path $demoRoot unaware
$aware = Join-Path $demoRoot aware
$badScore = Join-Path $demoRoot forged-score
$badSelection = Join-Path $demoRoot forged-selection

& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_4a.json --policy static_quality_unaware --output $unaware
& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_4a.json --policy static_quality_aware --output $aware
& $thesisPython .\verify_simulation.py $unaware
"Unaware verifier exit: $LASTEXITCODE"         # 0
& $thesisPython .\verify_simulation.py $aware
"Aware verifier exit: $LASTEXITCODE"           # 0

$firstHash = (Get-FileHash (Join-Path $unaware scenario.json) -Algorithm SHA256).Hash
$secondHash = (Get-FileHash (Join-Path $aware scenario.json) -Algorithm SHA256).Hash
"Same resolved bytes/hash: $($firstHash -eq $secondHash)" # True
Get-Content (Join-Path $unaware summary.json)
Get-Content (Join-Path $aware summary.json)
$decision = Get-Content (Join-Path $aware dispatch_decisions.jsonl) -Raw | ConvertFrom-Json
$decision.candidates | Format-List
$decision.selection_reason

Copy-Item $aware $badScore -Recurse
$path = Join-Path $badScore dispatch_decisions.jsonl
$decision = Get-Content $path -Raw | ConvertFrom-Json
$decision.candidates[0].score[1] = -1
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = ($decision | ConvertTo-Json -Depth 80 -Compress) + "`n"
[System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($path), $text, $utf8)
& $thesisPython .\verify_simulation.py $badScore
"Forged-score verifier exit: $LASTEXITCODE"    # 1
Get-Content (Join-Path $badScore verification.txt)

Copy-Item $aware $badSelection -Recurse
$path = Join-Path $badSelection dispatch_decisions.jsonl
$decision = Get-Content $path -Raw | ConvertFrom-Json
$decision.decision.assigned_products = @('P1')
$text = ($decision | ConvertTo-Json -Depth 80 -Compress) + "`n"
[System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($path), $text, $utf8)
& $thesisPython .\verify_simulation.py $badSelection
"Forged-selection verifier exit: $LASTEXITCODE" # 1
Get-Content (Join-Path $badSelection verification.txt)

git check-attr text eol -- tests/fixtures/checkpoint_4a/scenario.json
# text: set; eol: lf
```

## Call path: forecasting P1/P2 and choosing P2

`main()` → `run_once(..., policy=...)` → load unchanged scenario bytes → copy
execution policy → `run_inventory_simulation()` → fixed departure at 1 →
`dispatch_inventory_trip()`.

For unaware, existing release/ID sorting selects P1, and no operations/context
are supplied. For aware:

1. `build_forecast_context()` copies current time 1 and only P1/P2 released
   state, empty plant and known vehicle/calendar/trip records. The dispatcher
   records this snapshot and each request's operations in the actual input.
2. `choose_quality_aware_products()` enumerates P1 then P2 for payload round 0.
3. `forecast_candidate(context, trip, 'P1', [])` computes fixed arrival 3 using
   `predicted_delivery_time()`: two distance-one legs at speed 1 starting at 1.
   A copied plant admits P1 at 3. Its required WS1 cannot start until 8;
   projected wait is 8−3=5. The live plant remains empty at time 1.
4. `forecast_candidate(context, trip, 'P2', [])` independently copies the same
   starting state, also admits at 3, and sees WS2 available then. Wait is 0.
5. `quality_candidate_score()` gives `[0,5,0,'P1',0,1]` and
   `[0,0,0,'P2',0,1]`. Both have a start inside horizon 10; the second component
   therefore decides. P2 is selected, P1 is not reserved, and all candidate
   records plus comparison reason are saved.
6. Actual travel/pickup/admission/processing use unchanged physical functions.
   P2 is picked up at 2, admitted and starts at 3, finishes at 4.
7. Independent replay rebuilds this same decision-time observation from prior
   events, then independently projects both alternatives and checks score and
   selected IDs. It does not trust the saved predictions as expected values.

## Every new or changed simulation function

All have type hints and short docstrings. `Record` is a JSON-shaped dictionary.
Unless stated otherwise, functions change only copied/runtime memory, not files.

| File / function | Caller; receives → returns | Changes and rules |
| --- | --- | --- |
| `simulation/__main__.py` / `main()` | Python module entry; CLI → exit integer | Accepts both static policies, passes optional override; omitted policy uses source default. Existing seed/source guards remain. |
| `simulation/runner.py` / `run_once()` | CLI/tests; source/output paths, optional seed/policy → summary | Validates selected execution policy before output creation, copies execution inputs, records manifest policy and runs shared physics. Writes six artifacts while preserving original scenario bytes/hash. |
| `simulation/inventory_config.py` / `validate_inventory_scenario()` | Loader/resolver; scenario → None | Allows the two static policies; physical validation unchanged. No mutation. |
| `simulation/routing.py` / `dispatch_inventory_trip()` | Engine at departure; scenario/state/trip → feasibility bool | Keeps range checks, fixed routes and unaware behavior. For aware, builds actual operations/context input, calls selector, records full candidates/reason/tie and applies winning assignments. |
| `simulation/forecasting.py` / `build_forecast_context()` | Dispatcher/tests; scenario/state → new context record | Copies released-only definitions and state plus fixed physical inputs/departed-leg counts. Does not expose future returns or mutate live state. |
| `simulation/forecasting.py` / `predicted_delivery_time()` | Candidate projection; scenario/copied state/trip/departed-leg count → time | Starts from an active leg's scheduled arrival or current site and sums remaining fixed distances/speed; reset visits have zero service time. No mutation. |
| `simulation/forecasting.py` / `forecast_candidate()` | Selector; context/trip/candidate ID/prior selected IDs → predicted arrival/admission/first start/wait record | Deep-copies context state, inserts candidate and committed loads, advances completions/calendar/deliveries using actual plant/admission helpers. Returns first start within horizon or nulls/fallback. No live changes or files. |
| `simulation/forecasting.py` / `quality_candidate_score()` | Selector; candidate record → score list | Ranks horizon start, waiting/distance and stable ID/stops; fallback ignores unknown wait. No mutation. |
| `simulation/forecasting.py` / `choose_quality_aware_products()` | Dispatcher/tests; actual recorded policy input → selected IDs/candidate records/reason | Filters fixed-destination eligibility, fills payload greedily with conditional round projections and stable score minimum. Does not alter supplied input or physical state. |

Unchanged helpers on the new forecast path are `InventoryRunState` construction,
`network_site()`, `finish_inventory_operation()`, `start_inventory_operation()`
and `retry_inventory_admissions()`. They perform the same transitions explained
in earlier reviews, but here receive only isolated projection copies. Existing
CLI/runner loading, event engine, travel, pickup and final serialization paths
remain the same apart from choosing the policy; there is no second physical
simulation engine for the new policy.

## Every new or changed verifier function

All below are in `verify_simulation.py` and import no simulation/forecast code.

| Function/method | Caller; receives → returns | Changes and checks |
| --- | --- | --- |
| `validate_inventory_inputs()` | Format dispatcher; scenario → None | Independently accepts both supported static defaults; physical constraints unchanged. |
| `audit_forecast_context()` | Dispatch reconstruction; scenario/replayed state/departed-leg counts → independent context | Copies only released definitions/state and known physical inputs. No mutation. |
| `audit_delivery_time()` | Independent projection; scenario/state/trip/leg progress → arrival time | Recomputes remaining route geometry using own site lookup and math.hypot. No mutation. |
| `audit_candidate_forecast()` | Independent selector; context/trip/candidate/prior selections → predictions | Uses copied dictionaries and a chronological sweep, own capacity/holds/FIFO/calendar/completion/admission rules, with committed deliveries. No simulator helpers or live replay mutation. |
| `audit_quality_selection()` | Dispatch reconstruction; independently built input → selected/candidates/reason | Enumerates every eligible candidate/round and independently constructs the score tuple/tie; no physical mutation. |
| `audit_aware_dispatch_records()` | Reconciliation; expected/observed records/findings → None | Checks boundary data, candidate coverage/fields/scores, selected IDs and reason; appends precise product/round/time/event findings. Missing required fields raise incomplete evidence. |
| `InventoryAudit.capture_dispatch()` | Physical replay before assignment/departure/rejection; event/AV ID → None | Builds expected aware boundary and calls independent selector, or preserves unaware release/ID selection. Sets mandatory next physical assignments from independently selected IDs. |
| `InventoryAudit.reconcile()` | Coordinator after replay; saved evidence → recalculated summary | Adds precise aware audit before existing full-record comparison, keeping state/totals reconciliation. Only own state/findings change. |
| `verify_run()` | Standalone CLI; folder → report | Gets selected execution policy from manifest, checks allowed overrides, preserves exact-byte fingerprint and original generation audit, invokes physical/decision replay and reports. Missing policy evidence is incomplete; writes only two reports. |

All other verifier helpers retain their earlier contracts. Existing unaware
policy inputs are still checked against the exact quality-free expected schema.

## Every new test/helper

All methods are in `tests/test_static_quality.py`. unittest supplies only self
to test methods, which return None or raise assertions. All mutations/generation
are temporary and automatically cleaned; the committed fixture remains intact.

| Method | Caller; inputs/returns; files/state and checks |
| --- | --- |
| `setUp()` | unittest before each test; self → None. Creates scratch root/cleanup, scenario copy and numbering. |
| `run_policy()` | Tests; optional scenario/policy → folder/decisions/final state. Writes temporary common source bytes and invokes runner with execution override. |
| `verify_policy()` | Tests; folder/expected exit → report. Runs standalone verifier, checks process/report status and reads report. |
| `save_decisions()` | Mutation tests; folder/records → None. Rewrites only temporary dispatch JSONL as UTF-8/LF. |
| `test_requested_paired_example_and_identical_schedule()` | Checks P1/5 vs P2/0, same hashes/physical schedule/distance, exact predictions/scores, both PASS. |
| `test_forged_candidate_score_is_rejected()` | Changes P1 wait score to -1; checks precise independent expected/observed score/product/event finding. |
| `test_forged_selection_and_missing_candidate_are_rejected()` | Replaces P2 selection with P1 or hides P1 candidate; expects selection/coverage failure. |
| `test_committed_deliveries_holds_and_running_work_affect_forecasts()` | Projects committed P0 through route [1,2], held slot and final release; checks candidate admissions 6 and starts 8/7 when P0 is travelling or processing, then verifies actual results. |
| `test_forecasting_does_not_change_live_state_or_context()` | Builds actual boundary input from released state, calls selector and checks both live state and context unchanged. |
| `test_future_returns_are_outside_the_policy_boundary()` | Adds P3 release 1.5; requires its absence from requests, context definitions/state and candidates at departure 1. |
| `test_horizon_fallback_and_stable_product_id_ties()` | Tests one/no forecast starts and equal timing; checks horizon preference and stable ID tie rather than release tie. |
| `test_payload_rounds_include_previously_selected_products()` | Uses payload 2, checks selections P2/P1 and three conditional round records; verifies both products completed. |
| `test_unaware_boundary_does_not_receive_forecast_context()` | Checks no operations or context supplied to baseline and verifies unaware run. |
| `test_range_rejection_and_empty_trip_remain_static()` | Tests range failure and visit-only trip under aware policy; no candidates/assignments and correct physical verification. |
| `test_seeded_recycling_and_resolved_policy_replay()` | Runs aware on seeded recycling and replays with explicit same policy; checks physical/decision/scenario bytes equal and verifies both. |
| `test_cli_policy_override_preserves_input_hash()` | Runs both CLI policies on the same file and checks saved bytes/verification; rejects aware on legacy version 1. |
| `test_frozen_aware_fixture_lf_hash_and_isolated_verifier()` | Checks LF/hash, copies only standalone verifier outside repo, verifies isolated and preserves six evidence files. |
| `test_missing_manifest_or_candidate_evidence_is_incomplete()` | Removes manifest then required candidate evidence in separate restored stages; requires exit 2 without guessing policy. |
| `test_blocked_admission_uses_no_start_fallback()` | Holds the only slot through horizon, despite idle WS2; checks both admissions/starts null, ID fallback P1, retained onboard load and verifier PASS. |

## Changed files

- `.gitattributes`, `README.md`.
- New `configs/checkpoint_4a.json`.
- `simulation/__main__.py`, `inventory_config.py`, `routing.py`, `runner.py`.
- New `simulation/forecasting.py`.
- `verify_simulation.py`.
- New `tests/test_static_quality.py`.
- New six-file `tests/fixtures/checkpoint_4a/`: scenario.json,
  run_manifest.json, events.jsonl, dispatch_decisions.jsonl, summary.json,
  final_state.json. Its scenario is pinned to LF.
- New `docs/checkpoint_4a_review.md`.

No existing tests/fixtures changed. Generated results and temporary files remain
ignored. Stopped after checkpoint 4a for review.
