# Checkpoint 2: independent saved-run verifier

Branch `thesis-checkpoint-2` starts from `thesis-checkpoint-1` commit
`69a1a4d0815fe53ddb70911e4c35e9ff76ab646b`. No simulator files or checkpoint-one
tests were changed. The attached specification supplies the model rules;
implementation is limited to the user's checkpoint-two request.

## Design and limits

`verify_simulation.py` uses only the Python standard library. It imports no
simulation code, shares no simulation parsers or state classes, and does not run
the engine to decide whether a log is valid. Its own state is advanced from
validated logged transitions. The next permitted transition and its earliest
time come from the saved scenario and previously reconstructed state. This
checks omitted/duplicated/out-of-order actions, immediate inventory returns,
full calendar fits, payload, operation ordering, fixed plans and horizon handling.
The single product makes selection and contention ties trivial.

Checks cover all six checkpoint-one artifacts: scenario bytes/fingerprint,
manifest, release attributes, event sequence/time/identities, assignment,
transport geometry and load, pickup/admission, ordered operations and calendars,
shared-inventory waits, terminal records, actual travel through the horizon,
policy input/decision, every summary field and every exported final-state field.
Conservation is checked after each settled timestamp. Intermediate completion
and its inventory-return/terminal record are adjacent transitions at the same
time; conservation is checked after that timestamp has settled, rather than
between the two records.

Numeric comparisons use `math.isclose` with both relative and absolute tolerance
`1e-9`; dictionary keys and list order are exact. Boolean values do not compare
as numbers. Unsupported versions, scenario features, extra format fields,
missing evidence, unfinished manifests, malformed JSON and nonfinite JSON numbers are incomplete.
Known transition mismatches are violations. The checked scope is expressly the
single fixed journey, including speed changes, early horizons and zero-distance
legs. Later-checkpoint rules and full-program metrics are listed as unchecked.

A transition mismatch stops unsafe replay; subsequent totals are not claimed as
verified. The dispatch audit and other independently available checks can still
run. A proved violation takes precedence over incomplete checks. The report
lists replay completeness and unchecked coverage. Missing export files do not
prevent calculating totals when scenario/events remain available.

Only `verification.json` and `verification.txt` are written beside the input
artifacts, replacing previous verifier reports. The six input files are never
modified. A nonexistent run directory or unwritable report destination returns
2 with a console explanation; reports cannot be saved where no writable folder
exists. A readable folder with missing files receives both reports.

## Exact PowerShell reproduction

From the repository root after these changes are present, use your existing
`$thesisPython`, or initialize it as follows (Python 3.10+):

```powershell
# Only if .venv does not already exist:
py -3 -m venv .venv
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path

# Original five tests, then all seventeen tests:
& $thesisPython -m unittest discover -s tests -p test_checkpoint_1.py -v
& $thesisPython -m unittest discover -s tests -v

# The timestamp makes a fresh output root for each demonstration:
$demoRoot = Join-Path .\results ("checkpoint-2-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$original = Join-Path $demoRoot original
$speedTwo = Join-Path $demoRoot speed-2
$wrongStation = Join-Path $demoRoot wrong-workstation
$wrongCompletion = Join-Path $demoRoot wrong-completion
$missing = Join-Path $demoRoot missing-events

& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_1.json --output $original
& $thesisPython -m simulation --resolved-scenario .\configs\checkpoint_1_speed_2.json --output $speedTwo

& $thesisPython .\verify_simulation.py $original
"Original verifier exit: $LASTEXITCODE"       # 0
& $thesisPython .\verify_simulation.py $speedTwo
"Speed-2 verifier exit: $LASTEXITCODE"        # 0

# Wrong workstation, in a copy of the original:
Copy-Item -Path $original -Destination $wrongStation -Recurse
$eventPath = Join-Path $wrongStation events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$events[8].workstation_id = 'WS9'               # Event sequence 9
$lines = @($events | ForEach-Object { $_ | ConvertTo-Json -Depth 30 -Compress })
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $wrongStation
"Wrong workstation exit: $LASTEXITCODE"      # 1
Get-Content (Join-Path $wrongStation verification.txt)

# Wrong completion time, in a separate copy:
Copy-Item -Path $original -Destination $wrongCompletion -Recurse
$eventPath = Join-Path $wrongCompletion events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$events[9].time = 7                           # Event sequence 10
$events[9].end_time = 7
$lines = @($events | ForEach-Object { $_ | ConvertTo-Json -Depth 30 -Compress })
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $wrongCompletion
"Wrong completion exit: $LASTEXITCODE"       # 1
Get-Content (Join-Path $wrongCompletion verification.txt)

# Missing evidence, in another independent copy:
Copy-Item -Path $original -Destination $missing -Recurse
Remove-Item (Join-Path $missing events.jsonl)
& $thesisPython .\verify_simulation.py $missing
"Missing evidence exit: $LASTEXITCODE"       # 2
Get-Content (Join-Path $missing verification.txt)

Get-Content (Join-Path $original verification.json)
Get-Content (Join-Path $speedTwo verification.json)
```

These commands target Windows PowerShell 5.1 and PowerShell 7. Event edits use
explicit UTF-8 without BOM so encoding defaults do not obscure the intended
corruption. The reader also accepts UTF-8 with BOM. PowerShell is not installed
in the implementation environment: the Python commands and equivalent artifact
mutations were executed here; the PowerShell wrapper itself was not executed.

## Expected and observed demonstrations

| Case | Expected | Observed |
| --- | --- | --- |
| Original | PASS / 0, wait 3, distance 4, completed 1, unfinished 0 | Exact match |
| Speed 2 | PASS / 0, wait 5, distance 4, completed 1, unfinished 0 | Exact match |
| Wrong workstation | FAIL / 1, `workstation.identity`, sequence 9, time 4, expected WS1, observed WS9 | Exact match |
| Wrong completion | FAIL / 1, sequence 10, expected end 6, observed 7 | `events.transition_time` and `workstation.duration`, expected 6, observed 7 |
| Missing event log | INCOMPLETE / 2, name `events.jsonl` | `evidence.read`, expected readable events.jsonl, observed file-not-found |

Both reports contain findings with rule, time, sequence, relevant IDs and
expected/observed values. The original wait is `(8-6)+(12-11)=3`; speed 2 adds
`4-2=2` before WS1, yielding 5. These intervals are accumulated from verified
inventory entry/start event timestamps, not copied from the saved summary.

Original five tests: 5 passed. Full suite: 17 passed (12 new verifier tests).

## Call path and WS1 → WS3 example

`main()` → `verify_run()` → `read_evidence()` → independent scenario validation,
manifest checks, `replay_events()`, `audit_dispatch()`, `reconcile_exports()` →
`write_reports()`. The replay uses `expected_transition()` to calculate what
must be legal next, compares each actual record, and only then calls
`apply_transition()`. This path never calls a simulation-package function.

At sequence 9/time 4, WS1's start record has been checked against operation 1,
duration 2 and window [4,6]. Replay has P1 processing at WS1, index 0, empty
inventory, active end 6. Sequence 10/time 6 must be `operation_complete` with
WS1, index 0 and end 6. Applying it appends history, clears the active machine
and advances index to 1. The next required event is sequence 11 at time 6:
`inventory_return`, next index 1. Applying that puts P1 in RF1 inventory with
entry time 6.

`find_operation_opportunity()` now looks up route index 1, operation 3, WS3.
It independently computes `max(6,8)=8`, then confirms `8+3 <= 11`. Thus sequence
12 must start WS3 at 8 with end 11. Applying this start adds `8-6=2` inventory
wait, removes P1 from inventory, and records processing at WS3. The later WS2
start adds `12-11=1`. With speed 2, admission at 2 produces another wait of
`4-2=2` before this same transition sequence.

Changing sequence 9 to WS9 fails before state is advanced: the expected station
is derived from the route and saved calendar, not trusted from the event.
Changing sequence 10's completion/end to 7 fails against the active duration
and due time 6. Both findings retain sequence, time and product/station IDs.

## Every added verifier function

All functions are in `verify_simulation.py`. `Record` is a local JSON-shaped
type alias; it is not imported from the simulator. `EvidenceProblem` and
`ReplayViolation` are exception marker classes with no handwritten methods.

| Function | Who calls it / inputs → return | State or file effects |
| --- | --- | --- |
| `main()` | Python script entry; no parameters, reads CLI → exit integer | Calls `verify_run`, prints status/totals. Returns 2 for inaccessible folder/reports. |
| `verify_run()` | `main`; saved-run `Path` → report dictionary | Owns findings/evidence/replay coordination. Checks fingerprint/manifest and determines exit precedence. Calls report writer. |
| `read_evidence()` | Coordinator; folder and findings list → dictionary keyed by available filenames | Reads six artifacts, accepts UTF-8 BOM, parses JSON/JSONL, appends missing/unreadable/parse diagnostics. Never writes input files. |
| `parse_json_evidence()` | Reader for each JSON document/JSONL line; text → decoded JSON value | Parses JSON, walks the tree and rejects exponent overflow or numbers beyond finite checking range. No state/file mutation. |
| `reject_nonfinite_json()` | JSON parser via `parse_constant`; token string → no normal return | Raises `ValueError` for NaN/Infinity; reader records incomplete evidence. No state/file mutation. |
| `validate_supported_scenario()` | Coordinator; scenario → None | Independent scope, numeric, reference, route and window checks. Raises `EvidenceProblem` for unsupported/unusable input. Changes nothing. |
| `values_match()` | `check_value` and recursively itself; expected/observed JSON values → bool | Exact structure, ordered lists, tolerant finite numbers and type-aware booleans. No mutation. |
| `check_value()` | Manifest, replay, conservation, dispatch and export checks; findings/rule/expected/observed/optional event and IDs → bool | Calls comparator; appends a violation on mismatch. No physical-state or file changes. |
| `add_finding()` | Reader, comparator and coordinator; findings/category/rule/values/optional event and IDs → None | Appends diagnostic including context and IDs. No files. |
| `initial_replay_state()` | Replay; scenario → new state dictionary | Creates empty independent physical state; no input mutation. |
| `calculate_leg()` | Transition calculator; scenario/state/origin/target → new leg dictionary | Calculates coordinate distance and speed-based arrival, copies current load. No mutation. Example outbound distance 2, speed 2, departure 0 → arrival 1. |
| `find_operation_opportunity()` | Transition calculator; scenario/state → new operation dictionary or None | Chooses required operation's first full-fit window. No mutation. Example time 6/index 1 → WS3/start 8/end 11. |
| `expected_transition()` | Replay; scenario/state/current phase → `(due_time, event_type, expected_details)` | Uses reconstructed state and scenario only; derives legal next action, including horizon if no start fits. Calls geometry/calendar calculators. No mutation. |
| `apply_transition()` | Replay after all fields match; scenario/state/phase/logged event → next-phase string | Advances physical state, membership, milestones, operation index/history, travel and waiting. No files. The WS1→inventory→WS3 changes are traced above. |
| `check_conservation()` | Replay after a settled timestamp; scenario/state/context event/findings → None | Counts exclusive collection/AV/inventory/processing/terminal locations and checks payload. Only appends findings. |
| `replay_events()` | Coordinator; scenario/event list/findings → reconstructed state | Owns protocol phase, validates sequences/times/details and advances state. Handles partial horizon travel/wait. Missing evidence raises `EvidenceProblem`; proved mismatch raises `ReplayViolation` after recording findings. No files. |
| `audit_dispatch()` | Coordinator after replay attempt; scenario/events/decisions/findings → None | Independently checks the single fixed plan, boundary visibility, prior event anchor, proposal and trivial candidate/tie record. Missing records/fields are incomplete; forbidden quality information mismatches exact boundary schema. Only findings change. |
| `reconcile_exports()` | Coordinator after successful replay; state/evidence/findings → independently calculated summary | Derives counts/travel/wait, attaches summary to replay state, compares all saved summary and final-state fields. Does not write files or trust saved totals. |
| `write_reports()` | Coordinator; saved-run folder/report → None | Writes or replaces only `verification.json` and `verification.txt`, including scope/tolerance/findings/recalculated totals. |

Tricky comparison example: `check_value(findings, "workstation.identity",
"WS1", "WS9", event)` at sequence 9 returns False and appends expected WS1 /
observed WS9, time 4, sequence 9. Replay stops before applying the invalid start.
The coordinator can still audit dispatch, then chooses FAIL/1 even if another
file is missing. `recalculated_summary` remains null when replay cannot finish;
the report explicitly says final totals were not fully checked.

## Every added test helper and test

All methods below are in `tests/test_verifier.py`; each has type hints and a
short docstring. The unittest runner calls `setUp()` before each test and calls
test methods with only `self`. Each test returns None or raises on failed
assertions. All mutations are in temporary copies; the saved fixture is never
changed. The original five checkpoint-one methods are unchanged.

| Method | Caller / receives → returns | Changes and checks |
| --- | --- | --- |
| `setUp()` | unittest before each test; self → None | Allocates temporary directory, copies six valid fixture files, registers automatic cleanup. |
| `run_verifier()` | Tests; self, expected exit integer → parsed report | Runs standalone script in subprocess; verifier writes two temporary reports. Asserts process/report exit and text/JSON status agreement. |
| `change_event()` | Mutation tests; self, sequence, field, value → None | Changes one field by one-based sequence in copied `events.jsonl`. No source mutation. |
| `test_original_passes_without_modifying_evidence()` | unittest; self → None | Verifies fixed saved fixture; checks wait 3, empty findings and unchanged bytes for all six inputs. |
| `test_speed_two_passes_with_wait_five()` | unittest; self → None | Generates speed-2 run through existing CLI, invokes independent verifier, checks all six totals including wait 5. Temporary artifacts only. |
| `test_wrong_workstation_fails_with_event_details()` | unittest; self → None | Uses helper to set sequence 9 workstation to WS9; checks identity rule, time 4, sequence 9, expected WS1/observed WS9. |
| `test_wrong_completion_time_fails()` | unittest; self → None | Changes sequence 10 time/end to 7; checks duration finding expected 6/observed 7 and event 10. |
| `test_missing_evidence_is_incomplete()` | unittest; self → None | Removes each required file in turn, expects 2 and named missing file; restores bytes between subtests. |
| `test_violation_precedes_missing_evidence()` | unittest; self → None | Combines wrong workstation and missing summary; requires exit 1 and both violation/incomplete categories. |
| `test_summary_and_final_state_are_reconciled()` | unittest; self → None | Falsifies saved wait to 99 and nested remanufactured count to 0; checks independent wait 3 and both export findings. |
| `test_forbidden_quality_input_fails()` | unittest; self → None | Injects `[1,3,2]` into policy request; expects boundary-input violation. |
| `test_fingerprint_and_sequence_tampering_fail()` | unittest; self → None | Appends scenario newline and duplicates sequence 9; checks hash and sequence findings. |
| `test_unknown_formats_and_malformed_evidence_are_incomplete()` | unittest; self → None | Tries unsupported format, missing event field, broken JSON, NaN/overflow JSON and unfinished manifest; each returns 2. Restores evidence between subtests. |
| `test_verifier_runs_without_simulation_package()` | unittest; self → None | Copies only verifier outside repository, launches isolated Python (`-I`) there and verifies saved fixture successfully. Demonstrates no simulator import/runtime dependency. |
| `test_truncated_horizons_and_zero_distance()` | unittest; self → None | Generates and verifies horizons 0,1,7,9,13 and a zero-distance scenario at 14. Checks same-time legs, partial distance, waiting/processing retention and horizon-boundary completion. |

Tests that generate new runs use the unchanged existing simulation CLI. That
setup path is explained function by function in `checkpoint_1_review.md`;
verification itself never calls it. The checked-in saved fixture contains only
six minimal evidence files and is deliberate test data, not general generated
results. It was captured from checkpoint-one run `checkpoint-1-reviewed` and
retains its original scenario fingerprint and format. No verification reports,
virtual environment, scratch programs or results directory belong in source.

## Changed files

- New `verify_simulation.py`.
- New `configs/checkpoint_1_speed_2.json` (only vehicle speed differs from the original).
- New `tests/test_verifier.py`.
- Six new files in `tests/fixtures/checkpoint_1/`: `scenario.json`,
  `run_manifest.json`, `events.jsonl`, `dispatch_decisions.jsonl`, `summary.json`,
  `final_state.json`.
- New `docs/checkpoint_2_review.md` and updated `README.md`.

Checkpoint 2 is complete. Its source, tests, saved test fixture and documentation
are maintained on `thesis-checkpoint-2`; generated demonstrations remain ignored.
