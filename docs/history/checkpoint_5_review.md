# Checkpoint 5: seeded, matched-policy experiments

Created `thesis-checkpoint-5` from `thesis-checkpoint-4c` at
`593415e3b24af6a0bbaeb6daea350f2991621c0d`. Implemented checkpoint 5 only,
using Sections 10 and 12 of the supplied specification. Source, tests and documentation are maintained on `thesis-checkpoint-5`
for review. Existing single-run and independent-verifier code is unchanged.

## Input and output contract

`run_experiments.py` accepts one or more unresolved seeded condition configurations,
a positive replication count, an integer master seed, a fresh output directory,
and optionally `--policies`. Defaults are all four policies in static-unaware,
static-aware, dynamic-unaware, dynamic-aware order. `--config` accepts multiple
paths and may be repeated. Conditions get positional IDs C001, C002, etc.; same
filenames from different folders remain separate conditions. Validation happens
before creating the output root; an existing root is never overwritten by a batch.

This checkpoint reuses the existing format-two seeded configuration generator.
It does not expand generation to arbitrary format-three multi-point networks.
Such resolved inputs can still be run directly through the single-run command.
The supplied `configs/checkpoint_5.json` supports all four policies. It has two
AVs (capacity 8 each), one collection point, a remanufacturing plant, a recycler,
unlimited range, unlimited inventory, an operation ID 1 with duration 0.5,
and horizon 14. Arrivals have rate 4 on [0,1.8], batch size 1, equally weighted
remanufacturing/recycling classes. Static AV trips leave at 3 for CP1→RF1 and
CP1→RC1 respectively. Dynamic policies use the same physical inputs and choose
their own routes; they do not execute the static plans.

Replication IDs start at **1**. The stable seed protocol is:

```
encoded = UTF8("thesis-experiment-v1|" + str(master_seed) + "|" + str(replication_id))
replication_seed = unsigned_big_endian_integer(SHA256(encoded))
```

The seed deliberately excludes policy and condition ID. Conditions with the same
arrival settings therefore use the same arrival stream, while quality generation
can differ (for example when recycling is disabled). Exact scenario matching is
required within each condition/replication, not across different conditions.
Existing separately named `arrivals_batches` and `qualities` streams derive from
the replication seed. Calendars and network inputs are configured values, frozen
with the scenario; they are not sampled. Current policies are deterministic and
have no policy randomness or shared random generator.

The manifest stores the source and saved condition configuration, configuration
hash, generation settings, quality classes, collection-point weights and recycler
definitions. The saved configuration also preserves every fleet, capacity,
calendar and network setting. Each run records condition/replication, master and
derived seed, stream seeds and generation provenance, policy, expected/observed
scenario hashes, verification result/findings, relative artifact paths, Python
version/interpreter, and actual simulation/verifier argument arrays and cwd.
The existing generator's nested `seed_provenance.master_seed` denotes its input
seed, which here is the **derived replication seed**; the experiment master seed
remains explicitly recorded as 2026 in the experiment manifest and CSV.

Each single run is a separate child process launched with `--resolved-scenario`,
never `--config` or `--seed`. The independent verifier is also a separate child.
No engine, verifier checking logic or policy implementation was duplicated into
the batch program. Canonical generated scenario bytes are UTF-8/LF; scenario
matching and the existing verifier continue to hash the exact bytes.

```
results/checkpoint-5-review/original/
  experiment_manifest.json
  results.csv                 # every attempt, including failures/pending rows
  paired_results.csv          # only complete verified, hash-matched groups
  comparisons.csv             # one row per condition/replication; missing policies
  summaries.csv               # condition/policy means and sample standard deviations
  conditions/C001/
    configuration.json        # original condition bytes copied for provenance
    replication_0001/
      scenario.json           # THE frozen input shared by all four policy commands
      logs/                   # separate simulation/verifier stdout and stderr
      runs/<policy>/
        scenario.json         # byte-identical copy of the shared frozen input
        run_manifest.json
        events.jsonl
        dispatch_decisions.jsonl
        final_state.json
        summary.json
        verification.json
        verification.txt
    replication_0002/...
```

## One replication, from seed to four rows

1. `main()` parses the request and calls `run_experiments()`. Validation reads all
   conditions first; the runner creates a fresh root and saves the original config.
2. `derive_replication_seed(2026, 1)` returns the exact integer
   `108319335747130872897614666000385150515536794447511437542351997290053699714474`.
   It is the same regardless of the order in which policies will run.
3. `prepare_scenario(saved_configuration, derived_seed)` runs **once**, not four
   times. Its arrivals/batches stream seed is
   `37309643455189107782904538560755148565068385307966254924452345960906730222837`;
   its quality stream seed is
   `51386709168106433510147146311959639617436748492726625569146616139130337257575`.
   It generates P000001 at `1.6321599164426006` and P000002 at
   `1.7766912277383575`, batch size 1 each, both remanufacturable with operations [1].
   It serializes all products, batches, physical inputs and provenance to canonical
   scenario bytes. The experiment saves these bytes before launching any policy.
4. The shared input SHA-256 is
   `0e50ec220ed26e73461420e4da03abc1b0544d57c855bcae98372d84f2c76d9d`.
   `make_run_record()` gives each policy the same absolute frozen path and its own
   output directory. `execute_command()` launches `python -m simulation` four
   times, with the policy override as the only decision-policy difference.
5. Each child runs the existing CLI/runner. The runner reads the original scenario
   bytes and records their hash, uses an execution copy for policy selection and
   format-two→dynamic conversion, and saves the unchanged bytes in its run folder.
   Thus the static and dynamic output formats can differ while their input hash
   remains identical. The runner's summary, final state and logs are policy-specific.
6. `inspect_run()` hashes the run's saved scenario and shared frozen scenario,
   launches `verify_simulation.py`, and reads its report. All four return PASS/0.
   Verified metrics come from the independent report's recalculated summary.
7. `build_comparisons()` confirms all four selected members are completed, PASS,
   and hash-matched. `export_experiment()` writes four keyed rows for replication 1,
   and later four more for replication 2. Summary aggregation uses complete groups.

The four replication-1 rows contain these observed metrics (all hash-matched,
PASS, generated 2, remanufactured 2, recycled/unfinished 0):

| Policy | Distance | Plant inventory wait |
|---|---:|---:|
| static_quality_unaware | 4.414213562373095 | 0.5 |
| static_quality_aware | 4.414213562373095 | 0.5 |
| dynamic_quality_unaware | 2 | 0.5 |
| dynamic_quality_aware | 4 | 0 |

The static AVs both execute their fixed trips, including the empty recycler trip.
Dynamic unaware combines the two pickups; dynamic aware uses two AVs to avoid
waiting. Quality awareness is ranked by its prescribed wait score, not distance.

For replication 2 the input hash is
`a02949863924c943d6834dce130634d3acfc17448fb526aa78d7d5e1536bfd73`.
It generates two products: one remanufactured and one recycled, none unfinished.
Both static distances are `4.414213562373095`; both dynamic distances are
`3.434025603955953`; all plant inventory waits are zero.

To inspect matching inputs, open the shared `replication_0001/scenario.json`,
each of its four `runs/<policy>/scenario.json` files, the four run manifests and
the experiment manifest. Compare raw hashes, rather than reserializing JSON.
Open `results.csv` to retain exact seed text and join policy rows by
`(condition_id, replication_id)`. `paired_results.csv` is the ready-to-use
verified subset; `comparisons.csv` explains missing members. Summary CSV shows
attempted runs, individually verified runs, matched replications and exclusions,
then per-metric means and sample standard deviations. The latter are blank with
fewer than two matched observations; means are blank when there are none.

## Failure handling and observed acceptance checks

Every policy retains its artifacts and separate stdout/stderr logs. A simulation
crash is FAILED, a generation failure leaves explicit NOT_RUN records for every
policy in that replication, and verifier statuses distinguish PASS, FAIL,
INCOMPLETE, ERROR (crash/report mismatch) and NOT_RUN. Other policies/replications
continue. Missing or malformed evidence does not disappear from results.csv.

A verified comparison requires **every selected policy**, rather than allowing
summaries to mix different replication sets. If one policy is missing, even the
other individually valid members of that group are excluded from paired_results
and paired summaries. Their raw rows and individual verification statuses remain.
There is no policy imputation and no unpaired fallback. When selecting only two
policies, completeness is relative to those two.

`--refresh-verification --output <existing batch>` reruns only verifiers and
refreshes hashes, manifest and CSVs; it never regenerates inputs or reruns a
simulation. Original simulation status remains authoritative, so a crashed child
cannot become a successful simulation merely because leftover evidence verifies.
The refresh operates on evidence currently present and replaces verifier reports;
copy the batch first when experimenting with corrupted evidence.

| Case | Expected | Observed |
|---|---|---|
| Two replications × four policies | 8 runs, 2 complete groups, 8 PASS | Exactly that; exit 0 |
| Input hashes per replication | One hash for all four | One each, shown above |
| Fresh repeat, same seed | Same hashes and results | All 8 match; events/dispatch/final state/summary bytes also identical |
| Reversed policy order | Same hashes and results | All 8 match, including physical evidence bytes |
| Copy, remove rep-1 dynamic-aware events.jsonl, refresh | INCOMPLETE, named missing member, excluded first group | INCOMPLETE/2 for that verifier; batch exit 1; missing dynamic_quality_aware |
| Rows after removal | 8 raw, 4 paired (rep 2 only) | 8 raw, 4 paired; summaries each n=1 |
| Forged saved distance (test) | FAIL excluded | FAIL; static_quality_unaware named missing |
| Self-consistent run with different raw input bytes (test) | Excluded even if verifier passes | PASS report but hash-match false, comparison incomplete |
| Existing + new tests | 104 + 14 pass | 118 pass |

The missing-evidence finding is `evidence.read`, file `events.jsonl`, expected
`Readable events.jsonl`, observed file-not-found. The surviving raw scenario
hashes still match: hash matching is reported separately from verification
completeness. The affected policy has `included_in_verified_comparison=false`;
so do its three otherwise-valid partners in replication 1.

Batch exit codes are 0 for complete verified comparisons, 1 for completed batches
with exclusions, and 2 for setup/argument errors. The individual verifier keeps
its own 0/1/2 contract. Optional `--no-verify` explicitly records NOT_RUN, produces
no verified paired data and returns 0 only if simulations completed/hash-matched;
a later refresh can actually verify those runs. Verification is enabled by default.

## Exact PowerShell commands

Run in the repository, with fresh output paths. Keep your existing interpreter
assignment or use the local virtual environment below. Seed columns in CSV are
strings; retain that exact text when copying these large derived integers.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $thesisPython -m unittest discover -s tests -v
# Expected: Ran 118 tests ... OK (104 existing + 14 new)
& $thesisPython -m unittest discover -s tests -p test_experiments.py -v
# Expected: Ran 14 tests ... OK

$study = Join-Path (Get-Location) 'results\checkpoint-5-review'
$original = Join-Path $study 'original'
$repeat = Join-Path $study 'repeat'
$reversed = Join-Path $study 'reversed'
& $thesisPython run_experiments.py --config configs/checkpoint_5.json --replications 2 --seed 2026 --output $original
$LASTEXITCODE # 0: 8 runs; 2/2 complete verified comparisons
& $thesisPython run_experiments.py --config configs/checkpoint_5.json --replications 2 --seed 2026 --output $repeat
$LASTEXITCODE # 0
& $thesisPython run_experiments.py --config configs/checkpoint_5.json --replications 2 --seed 2026 --output $reversed --policies dynamic_quality_aware dynamic_quality_unaware static_quality_aware static_quality_unaware
$LASTEXITCODE # 0

$baselineRows = @(Import-Csv "$original\results.csv" | Sort-Object condition_id,replication_id,policy)
foreach ($other in @($repeat, $reversed)) {
    $otherRows = @(Import-Csv "$other\results.csv" | Sort-Object condition_id,replication_id,policy)
    $differences = Compare-Object ($baselineRows | ConvertTo-Csv -NoTypeInformation) ($otherRows | ConvertTo-Csv -NoTypeInformation)
    $null -eq $differences # True for both: every CSV value matches
}
$baselineRows | Group-Object condition_id,replication_id | ForEach-Object {
    [pscustomobject]@{
        Comparison = $_.Name
        Rows = $_.Count
        UniqueHashes = @($_.Group.scenario_sha256 | Select-Object -Unique).Count
        AllPass = @($_.Group | Where-Object verification_status -ne 'PASS').Count -eq 0
    }
} # Each group: Rows 4, UniqueHashes 1, AllPass True

$replication = Join-Path $original 'conditions\C001\replication_0001'
Get-FileHash "$replication\scenario.json"
Get-ChildItem "$replication\runs\*\scenario.json" | Get-FileHash
# All five: 0E50EC220ED26E73461420E4DA03ABC1B0544D57C855BCAE98372D84F2C76D9D
Import-Csv "$original\results.csv" | Format-Table replication_id,policy,verification_status,vehicle_distance,plant_inventory_wait
Import-Csv "$original\summaries.csv" | Format-Table condition_id,policy,matched_replications,vehicle_distance_mean,plant_inventory_wait_mean
Get-Content "$original\experiment_manifest.json"

# Deliberately incomplete evidence in a separate copy, preserving the valid batch:
$incomplete = Join-Path $study 'incomplete'
Copy-Item $original $incomplete -Recurse
Remove-Item "$incomplete\conditions\C001\replication_0001\runs\dynamic_quality_aware\events.jsonl"
& $thesisPython run_experiments.py --output $incomplete --refresh-verification
$LASTEXITCODE # 1: 8 runs; 1/2 complete verified comparisons
Import-Csv "$incomplete\comparisons.csv" | Format-Table
# Replication 1 complete=False, missing_policies=dynamic_quality_aware
# Replication 2 complete=True, missing_policies blank
(Import-Csv "$incomplete\results.csv" | Measure-Object).Count # 8
(Import-Csv "$incomplete\paired_results.csv" | Measure-Object).Count # 4
Get-Content "$incomplete\conditions\C001\replication_0001\runs\dynamic_quality_aware\verification.txt"

# Rerun one recorded simulation into a new output folder:
$manifest = Get-Content "$original\experiment_manifest.json" -Raw | ConvertFrom-Json
$member = $manifest.runs | Where-Object { $_.replication_id -eq 1 -and $_.policy -eq 'dynamic_quality_aware' } | Select-Object -First 1
$member.simulation.command # Stored argv; replace its --output value before reuse
$single = Join-Path $study 'single-replay'
& $thesisPython -m simulation --resolved-scenario (Join-Path $original $member.resolved_scenario) --policy $member.policy --output $single
& $thesisPython verify_simulation.py $single
$LASTEXITCODE # 0

# Optional subset; multiple conditions can follow --config or use repeated --config:
& $thesisPython run_experiments.py --config configs/checkpoint_5.json configs/checkpoint_3b_no_recycling.json --replications 2 --seed 2026 --policies static_quality_unaware dynamic_quality_aware --output results/checkpoint-5-subset
```

Python equivalents were executed here; PowerShell itself was not available on
this Linux machine. PowerShell JSON readers may display very large integers
approximately; CSV retains the exact seed text, and the saved command replays the
frozen scenario without any need to re-enter its seed. Leave the manifest/scenario
bytes intact when comparing fingerprints. After relocating an experiment to
another machine, use its relative scenario/run paths with that machine's
`$thesisPython`; historical argv/cwd intentionally record the original execution.

## Every new function, method and direct project dependency

No existing function is changed in this checkpoint. All functions have type hints
and short docstrings. `Record` is a dictionary alias. The tables below identify
callers, inputs, return values and side effects, including nested fault helpers.

### run_experiments.py

| Function | Who calls it; input → return | Effects and rules |
|---|---|---|
| `main()` | Script invocation; argv → integer exit code | Parses batch/refresh modes, rejects conflicting flags, delegates work, prints counts/error. No direct evidence mutation. |
| `derive_replication_seed()` | Validation and batch loop/tests; integer master, positive replication ID → integer | Pure SHA-256 derivation shown above; rejects bool/non-integer/zero index. No mutable RNG shared between policies. |
| `validate_experiment_inputs()` | Batch before mkdir; configuration Paths, count, seed, policies → source Path/bytes/parsed-config tuples | Reads/validates every condition; rejects zero count, empty/duplicate/unsupported policies, unsupported generation inputs; writes nothing. |
| `read_json_object()` | Validation, inspection, refresh; Path → dictionary | Reads UTF-8 (BOM accepted), rejects non-object JSON, does not rewrite bytes. |
| `write_json()` | Batch/export/refresh; Path, dictionary → None | Writes UTF-8/LF temporary sibling then atomically replaces JSON export; disallows NaN. |
| `make_run_record()` | Batch per policy; condition, replication, seeds, policy, scenario Path, root → pending run dictionary | Computes distinct relative output path and exact child argv; no I/O or simulation. |
| `execute_command()` | Batch/inspection; argv, log-prefix Path → process metadata | Runs child in repository cwd, writes separate stdout/stderr, records nonzero exits and launch OSError instead of aborting other runs. Does not precreate the child's run folder. |
| `inspect_run()` | Batch after child or refresh; root, mutable run record, verify flag → None | Reads saved/frozen hashes and raw metrics, launches verifier by default, updates record with status/findings and independently recalculated metrics for PASS. Invalid/missing evidence remains visible. Disabled or uncompleted simulation gets NOT_RUN. Hash matching requires both files to match the recorded expected hash. |
| `build_comparisons()` | Export; manifest → comparison dictionaries | Marks each member included only for a complete selected-policy group. Reads statuses and raw-hash checks; records missing IDs and exclusion reasons. Distinguishes matching hashes from complete verification. |
| `write_csv()` | Export; Path, columns, row dictionaries → None | Replaces UTF-8 CSV with header/LF and explicit field ordering. |
| `export_experiment()` | Batch after each attempt and finalization, refresh; root/manifest → None | Builds comparisons, mutates included flags/comparisons, writes four CSVs plus manifest. Keeps all rows; means/sample-stdev use only complete matched groups. |
| `run_experiments()` | Main/tests; conditions, count, master seed, fresh root, optional policies/verify → manifest | Creates root/copies configs; derives each seed, resolves once, freezes bytes; creates pending rows, launches each policy, inspects, exports incrementally. Handles generation errors and child failures without dropping rows. Changes only output tree. |
| `refresh_verification()` | Main/tests; existing root → refreshed manifest | Reads existing manifest, reruns only verifiers/hash checks, updates exports/status. Does not mutate frozen scenarios, simulation logs/status, events or saved results. |

A concrete `inspect_run(root, dynamic_aware_record)` after deleting its events
log still finds identical raw scenario bytes, but receives verifier INCOMPLETE/2
and records `evidence.read`. `build_comparisons()` then reports that policy missing,
while `export_experiment()` leaves all eight raw rows and only four paired rows.

### Existing project functions called at the new boundary

These are reused unchanged. Earlier checkpoint guides describe the physics and
policy internals invoked by the existing single-run entry point.

| Function / file | Caller; receives → returns | State/files changed |
|---|---|---|
| `validate_generation_config()` / simulation/return_generation.py | Batch validation and preparation; parsed config → None/error | Read-only checks of arrivals, batches, quality weights/disposition and physical schema; filters eligible class mass for validation. |
| `validate_weights()` / same | Generation validation; weights/positive-mass flag → None/error | Read-only finite/nonnegative/positive-sum guards. |
| `validate_inventory_scenario()` / simulation/inventory_config.py | Generation validation and resolved preparation; scenario → None/error | Read-only constraints on vehicles, static trips, calendars, inventories, operation support and products. |
| `prepare_scenario()` / simulation/return_generation.py | Batch once per condition/replication; saved config Path and derived seed → resolved dictionary and canonical bytes | Reads config, uses local independent generators, validates resolved output; writes no files. Batch saves its bytes. |
| `derive_stream_seed()` / same | prepare_scenario; derived seed and stream name → integer | Pure SHA-256 named-stream derivation; names arrivals_batches and qualities. |
| `generate_return_batches()` / same | prepare_scenario; config, arrivals Random → batch list | Advances only that local RNG; uses exponential intervals and fixed/weighted batch sizes; respects configured end time. |
| `generate_products()` / same | prepare_scenario; config, frozen batch list, quality Random → products | Advances only quality RNG; filters recycling-only classes before sampling when no recyclers, renormalizes; no file/state mutation. |
| `weighted_index()` / same | Batch/quality generation; Random and weights → selected index | Consumes one random draw, cumulative weighted choice. Not used for the example's fixed batch size. |
| `main()` / simulation/__main__.py | Each simulation child; saved path/policy/output argv → exit integer | Calls existing run_once, prints summary/error. |
| `run_once()` / simulation/runner.py | Simulation CLI; frozen Path/output/policy → summary | Creates isolated six-file run evidence; uses original input bytes, policy execution copy, existing engine; no replication loop. |
| `load_scenario()` / simulation/config.py | run_once; frozen Path → scenario/bytes | Reads and validates exact saved bytes, no rewriting. |
| `validate_scenario()` / same | load_scenario; scenario → None/error | Selects version-specific read-only validation. |
| `main()` / verify_simulation.py | Each verifier child; run-folder argv → 0/1/2 | Calls verify_run and prints status/calculated summary. |
| `verify_run()` / same | Verifier CLI; run-folder Path → report | Independently reads/replays evidence and raw-byte hash, writes verification.json/txt; no simulation import. |

### tests/test_experiments.py

`unittest` calls `setUp()` and each test without arguments. Tests return None.
All mutations/faults occur in automatically cleaned temporary output trees;
no test edits the source configuration or repository evidence fixtures.

| Method/helper | Caller, input/return and effects |
|---|---|
| `setUp()` | unittest → None; allocates TemporaryDirectory, cleanup and unique counter. |
| `run_batch()` | Tests; count/extra argv → root/manifest; runs real CLI with seed 2026, checks exit 0, reads manifest and increments counter. |
| `refresh()` | Tests; root/expected exit → manifest; runs refresh CLI, changes temporary reports/exports only. |
| `read_rows()` | Tests; root/CSV filename → list of string-valued rows; read only. |
| `test_stable_seed_derivation()` | Pure seed calls; pins known seed, changed seed/index and invalid numeric inputs; no files. |
| `test_two_replications_four_policies_and_csv_summaries()` | Real eight-run batch; asserts raw bytes/hash/status/seeds/counts, eight raw and paired rows, four summaries and means; temp files. |
| `test_repeat_and_reversed_policy_order()` | Runs three eight-run batches; keys by replication/policy and compares hashes, summaries and full physical evidence bytes despite policy order; temp files. |
| `test_multiple_conditions_record_generation_difference()` | Writes a no-recycler condition with same filename, runs two conditions; checks distinct IDs, recorded filtering and identical arrival batches. |
| `test_policy_subset_has_selected_members_only()` | Runs aware-dynamic/unaware-static subset; checks only selected members needed for complete group. |
| `test_missing_file_refresh_excludes_entire_affected_comparison()` | Deletes one temp events.jsonl, refreshes; asserts INCOMPLETE, named policy, hashes still match, eight raw/four paired rows, rep-2-only summaries, retained final state. |
| `test_forged_totals_refresh_is_failed_not_incomplete()` | Changes temp saved distance; refreshes; asserts FAIL, missing member, empty paired data/means. |
| `test_self_consistent_wrong_scenario_hash_still_excluded()` | Appends newline to one run scenario and updates that run's own fingerprint; verifier passes semantic evidence but batch rejects mismatch against frozen bytes. |
| `test_simulation_crash_retains_artifacts_and_continues()` | Uses nested fail_one fault helper; asserts FAILED status, retained artifacts/logs, subsequent policies continue, refresh cannot bless a failed simulation. |
| `fail_one()` (nested above) | Patched execute_command; argv/log prefix → actual process record with simulated exit 7 for static-aware. Real child first writes evidence; only metadata changed. |
| `test_verifier_crash_is_error_and_does_not_abort_batch()` | Uses nested crash_verifier; checks every crashed verifier is ERROR, all policy members explicitly missing, artifacts retained. |
| `crash_verifier()` (nested above) | Patched execute_command; argv/log prefix → process record; substitutes real Python exit-nine child for verifier calls, delegates real simulations. |
| `test_no_verify_never_claims_verified_results_and_can_refresh()` | Runs opt-out; asserts NOT_RUN and no paired rows, refreshes to actual PASS and four paired rows. |
| `test_generation_failure_has_rows_and_later_replication_runs()` | Injects first prepare error, returns a valid second resolved input; asserts exactly two prepare calls, eight raw rows, four NOT_RUN records, second group complete. |
| `test_individual_manifest_command_replays_exact_scenario()` | Copies recorded argv, replaces only output path, invokes child, compares frozen bytes/events/dispatch/state/summary with original. |
| `test_bad_requests_do_not_overwrite_or_create_batches()` | Rejects zero count, duplicate/empty policies, unsupported resolved config and conflicting refresh flags; confirms existing directory marker untouched. |

## Changed files and stopping point

- `run_experiments.py`: new separate batch/refresh entry point and exports.
- `configs/checkpoint_5.json`: small seeded condition runnable with every policy.
- `tests/test_experiments.py`: 14 tests, four methods supporting tests, two nested fault helpers.
- `docs/checkpoint_5_review.md`: this reproduction and function walkthrough.
- `README.md`: checkpoint-5 usage and file overview.

Generated examples remain under ignored `results/checkpoint-5-review`; test logs
are under ignored `work`. No virtual environment, generated result, or new hashed
scenario fixture is added to source control. Existing LF rules and raw-byte
verifier checks are unchanged. Stopped after checkpoint 5 for review; no visualization or later checkpoint work
is included.
