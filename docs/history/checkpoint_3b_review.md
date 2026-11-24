# Checkpoint 3b — seeded returns and optional recycling

Branch `thesis-checkpoint-3b` was created from `thesis-checkpoint-3a` at
`84eebc0075017f3c84d928a97cd00b940d8ac895`. Only checkpoint 3b is implemented.
Source, tests and documentation are maintained on `thesis-checkpoint-3b`.

## Inputs, streams and replay

Use `--config PATH --seed INTEGER` to generate one run, or
`--resolved-scenario PATH` to replay frozen inputs. These sources are mutually
exclusive. Config requires an explicit seed; resolved replay rejects a seed
override. Policy remains `static_quality_unaware`. There is no replication loop.

Both supplied configurations use one plant RF1 `(0,0)`, one collection point
CP1 `(1,0)`, horizon 14, one duration-0.5 workstation open `[0,14)`, unlimited
plant inventory/range, and two payload-100 vehicles. Both trips depart at 4 and
visit CP1. AV1 delivers to RF1. In the enabled case AV2 delivers to RC1 `(0,1)`;
in the disabled case it delivers to RF1. Both use arrival rate 2 over `[0,3]`,
batch sizes 1 or 2 with equal weights, and positive equal weights for QM
(remanufacture `[1]`) and QR (recycle `[]`). QR remains configured when recycling
is disabled; it is removed from the effective sampling distribution.

Resolved inputs retain the generation settings, quality definitions/overrides,
products with batch IDs, batch records, and complete seed provenance. The named
streams are derived independently using the big-endian SHA-256 integer of:

```text
thesis-return-v1|42|arrivals_batches
thesis-return-v1|42|qualities
```

`arrivals_batches` draws exponential interarrival times and batch sizes; quality
sampling uses only `qualities`. Generate all batches first, then independently
sample every item's quality. Non-recycling runs filter recycling classes before
sampling and explicitly normalize remaining weights. They never generate/drop
recycling items as a rejection step. Collection-point `quality_weights` may
override configured class weights. A positive stream with no positive effective
weight is an error. Batch size can be a positive fixed integer or a distribution
with positive integer values and finite nonnegative weights with positive sum.

For arrival uniform U and rate r, the interval is `-log1p(-U)/r`. Each weighted
CDF sample consumes exactly one uniform. Quality classes are ordered by ID.
Resolved bytes are canonical UTF-8 JSON, sorted keys, indentation 2 and LF. The
fresh run parses those exact canonical bytes back before executing, so replay
also has the same dictionary iteration order. This makes not just outcomes but
saved event/decision bytes repeatable. Run manifest IDs reflect their output
folder and therefore are intentionally not byte-identical across runs.

Seed provenance records the master seed, both derived stream seeds, generation
version and algorithm. The verifier independently derives those seeds and
reconstructs batches/quality samples from saved settings using only the standard
library. This checks algorithm conformance, not statistical validity of the
chosen distribution. The physical replay then checks actual release attributes
against the resolved products. Missing generation evidence is incomplete; known
seed/input inconsistencies are violations. Existing byte-for-byte scenario
fingerprinting remains unchanged; no line-ending normalization precedes hashing.

Version-two resolved scenarios from checkpoint 3a and version-one legacy inputs
remain supported. Old tests/fixtures are unchanged. The new saved scenario
fixture is pinned to LF in `.gitattributes`.

## Actual results, with expected comparisons

All original 30 tests and 14 new tests pass: 44 total.

| Check | Expected | Observed |
| --- | --- | --- |
| Same config and seed repeated | Same generated products/scenario | Byte-identical |
| Saved scenario replay | Same events/decisions/totals/final state | Byte-identical |
| Enabled versus disabled arrivals | Same batches/release times/sizes and item IDs | Nine identical batches, 14 products |
| Disabled recycling | Zero recycling-only products/acceptances | 0 |
| Enabled recycling | At least one eligible product accepted | 7 |
| Enabled verifier | PASS / exit 0 | PASS / exit 0 |
| Disabled verifier | PASS / exit 0 | PASS / exit 0 |
| Remanufacturable product in acceptance record | FAIL / exit 1, disposition violation | `recycling.disposition`, sequence 59, expected recycle, observed remanufacture |

Final enabled totals: generated 14, remanufactured 7, recycled 7, unfinished 0,
distance `4.414213562373095`, inventory wait 10.5, waiting to unload 0.
Disabled totals: generated 14, remanufactured 14, recycled 0, unfinished 0,
distance 4, inventory wait 45.5, waiting to unload 0.

The paired batch records are identical (times displayed rounded here only):

| Release | Size |
| --- | --- |
| 0.05082716154543322 | 2 |
| 0.5676968513057951 | 2 |
| 1.1514903425493204 | 1 |
| 1.5046305022123316 | 2 |
| 1.873462063843127 | 2 |
| 2.4491095964385297 | 1 |
| 2.537446378680471 | 1 |
| 2.7545431076144737 | 1 |
| 2.9416812035014677 | 2 |

## Exact PowerShell reproduction

From the repository root with these changes present and your existing venv:

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path

# The original 30 tests, then 14 new tests, then all 44:
& $thesisPython -m unittest discover -s tests -p test_checkpoint_1.py -v
& $thesisPython -m unittest discover -s tests -p test_verifier.py -v
& $thesisPython -m unittest discover -s tests -p test_inventory.py -v
& $thesisPython -m unittest discover -s tests -p test_return_generation.py -v
& $thesisPython -m unittest discover -s tests -v

$demoRoot = Join-Path .\results ("checkpoint-3b-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$enabled = Join-Path $demoRoot enabled
$disabled = Join-Path $demoRoot disabled
$repeat = Join-Path $demoRoot repeat
$replay = Join-Path $demoRoot replay
$invalid = Join-Path $demoRoot invalid-recycling

& $thesisPython -m simulation --config .\configs\checkpoint_3b_recycling.json --seed 42 --output $enabled
& $thesisPython -m simulation --config .\configs\checkpoint_3b_no_recycling.json --seed 42 --output $disabled
& $thesisPython -m simulation --config .\configs\checkpoint_3b_recycling.json --seed 42 --output $repeat
& $thesisPython -m simulation --resolved-scenario (Join-Path $enabled scenario.json) --output $replay

foreach ($run in @($enabled, $disabled, $repeat, $replay)) {
    & $thesisPython .\verify_simulation.py $run
    "Verifier exit: $LASTEXITCODE"            # 0 for all four
}

foreach ($file in @('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json')) {
    $firstHash = (Get-FileHash (Join-Path $enabled $file) -Algorithm SHA256).Hash
    $repeatHash = (Get-FileHash (Join-Path $repeat $file) -Algorithm SHA256).Hash
    $replayHash = (Get-FileHash (Join-Path $replay $file) -Algorithm SHA256).Hash
    "$file repeat=$($firstHash -eq $repeatHash) replay=$($firstHash -eq $replayHash)"
    # Both True for every file
}

$on = Get-Content (Join-Path $enabled scenario.json) -Raw | ConvertFrom-Json
$off = Get-Content (Join-Path $disabled scenario.json) -Raw | ConvertFrom-Json
$onBatches = $on.arrival_batches | ConvertTo-Json -Depth 30 -Compress
$offBatches = $off.arrival_batches | ConvertTo-Json -Depth 30 -Compress
"Equal batches: $($onBatches -eq $offBatches)" # True
"Disabled recycle products: $(@($off.products | Where-Object { $_.disposition -eq 'recycle' }).Count)" # 0

# Corrupt only a copied event log; scenario bytes/hash remain intact:
Copy-Item $enabled $invalid -Recurse
$reman = $on.products | Where-Object { $_.disposition -eq 'remanufacture' } | Select-Object -First 1
$eventPath = Join-Path $invalid events.jsonl
$events = @(Get-Content $eventPath | ForEach-Object { $_ | ConvertFrom-Json })
$acceptance = $events | Where-Object { $_.event_type -eq 'recycler_acceptance' } | Select-Object -First 1
$acceptance.product_id = $reman.id
$lines = @($events | ForEach-Object { $_ | ConvertTo-Json -Depth 40 -Compress })
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines([System.IO.Path]::GetFullPath($eventPath), [string[]]$lines, $utf8)
& $thesisPython .\verify_simulation.py $invalid
"Invalid verifier exit: $LASTEXITCODE"        # 1
Get-Content (Join-Path $invalid verification.txt)

Get-Content (Join-Path $enabled summary.json)
Get-Content (Join-Path $disabled summary.json)
git check-attr text eol -- tests/fixtures/checkpoint_3b/scenario.json
```

Python commands and equivalent mutations ran in the implementation environment.
PowerShell is unavailable there, so the wrapper was not executed there. The
syntax targets Windows PowerShell 5.1/7 and writes copied logs explicitly as
UTF-8/LF rather than depending on PowerShell's default encoding.

## Trace P000001 from generation to recycler acceptance

1. CLI `main()` calls `run_once(config_path, output_path, seed=42)`.
   `prepare_scenario()` validates configuration, derives the streams and invokes
   batch generation followed by quality generation. Neither generator writes
   files or sees dispatcher execution.
2. For `derive_stream_seed(42, 'arrivals_batches')`, the seed's hex value is
   `282940e54703cc3eeb24723a43a07cccb40accad0f609407daa3ecb3e740304f`.
   Its first uniform is `0.09665823790798789`; rate 2 gives release
   `-log1p(-0.09665823790798789)/2 = 0.05082716154543322`.
   The next uniform is `0.9531943825280725`; equal batch weights select size 2.
   B000001 therefore contains P000001 and P000002.
3. The independent quality stream's seed hex is
   `c1ac1ea47fd398b1c9901b51b018e42ae7e7af71b186362470e50bb2ac2a7d70`.
   Its first uniform is `0.6177110613081647`. Enabled classes QM and QR have
   cumulative probabilities 0.5 and 1, so P000001 is QR, recycle, operations `[]`.
   With no recyclers, QR is removed first; QM has probability 1 and the same
   first product becomes remanufacturable without altering its release or batch.
4. The runner saves canonical `scenario.json` and its byte hash, then calls
   `run_inventory_simulation()`. At release, event 1 changes P000001 from
   unreleased to at-collection/CP1 and records the resolved attributes.
5. At 4, `dispatch_inventory_trip()` uses only released request ID/origin/time/
   disposition. AV1 selects remanufacturing loads; AV2's fixed destination RC1
   selects recycling loads. Sequence 23 assigns P000001 to AV2 with planned RC1;
   the product remains physically at CP1.
6. `depart_inventory_leg()` logs AV2's departure at sequence 30. Distance RF1→CP1
   is 1 at speed 1, so it arrives at 5 (sequence 40).
   `collect_inventory_products()` moves P000001 onboard and logs pickup 41.
7. `delivery_site(..., 'RC1')` returns `(0,1)`. The inbound leg from `(1,0)` has
   distance `sqrt(2)`. Sequence 48 departs at 5; sequence 58 arrives at
   `5+sqrt(2)=6.414213562373095`.
8. `accept_recycling_products()` checks the actual disposition, empty operations
   and committed destination, removes P000001 from AV2, sets recycled with no
   physical location, stores actual destination RC1 and recycling timestamp,
   then logs acceptance sequence 59. The product never enters plant inventory
   and has no workstation history. It contributes one terminal recycled count.
9. The independent audit reconstructs the streams and release, assignment,
   movement, load and acceptance. It checks disposition BEFORE accepting the
   event, then compares its counts/state with the exports. Changing event 59's
   ID to remanufacturable P000002 yields `recycling.disposition`, expected
   recycle, observed remanufacture, and exit 1.

## Every new or changed simulation function

All inputs/outputs have type hints and functions have short docstrings.
`Record` is a JSON-shaped dictionary. Unless stated otherwise, transition
functions mutate only the passed run state and its audit records, not files.
Unchanged functions on the call path retain their earlier review contracts.

| File / function | Caller; receives → returns | State/files and behavior |
| --- | --- | --- |
| `simulation/__main__.py` / `main()` | Python module entry; CLI → exit integer | Parses mutually exclusive sources, requires/rejects seed appropriately, invokes runner, prints totals. Bad CLI combinations exit 2 before creating output. |
| `simulation/runner.py` / `run_once()` | CLI/tests; source/output paths, optional keyword seed → summary | Calls loader when seed is absent or resolver when supplied, then existing engine/export path. Writes six artifacts in a new directory; preserves exact scenario bytes and hash. |
| `simulation/return_generation.py` / `prepare_scenario()` | Runner; config path/integer seed → resolved record and canonical bytes | Reads config, validates, creates independent PRNGs, builds batches/products/provenance, validates resolved physical inputs and parses its canonical bytes back. Writes no files. |
| `simulation/return_generation.py` / `derive_stream_seed()` | Resolver; master integer/name → derived integer | SHA-256 of the documented UTF-8 namespace; no mutation or random draw. |
| `simulation/return_generation.py` / `validate_generation_config()` | Resolver/tests; unresolved record → None | Rejects pre-resolved inputs, invalid arrival/batch/quality settings, unknown overrides, missing effective quality mass and unsupported positive routes; calls physical validator on an empty-products copy. No input mutation. |
| `simulation/return_generation.py` / `validate_weights()` | Config validator; weights/positive-mass requirement → None | Checks finite nonnegative elements, finite sum and optional positive sum. No mutation. |
| `simulation/return_generation.py` / `weighted_index()` | Batch/quality generators; supplied Random and weights → index | Advances only that PRNG by one uniform; cumulative-CDF choice, zero weights never selected. |
| `simulation/return_generation.py` / `generate_return_batches()` | Resolver; config/arrival-batch PRNG → batch records | Advances only arrival/batch PRNG. Samples Poisson interarrivals until configured end, then fixed/weighted positive quantities. Assigns stable batch/item IDs; never reads quality classes. |
| `simulation/return_generation.py` / `generate_products()` | Resolver; config/frozen batches/quality PRNG → product records | Filters recycling classes before sampling when disabled, normalizes effective weights, samples independently per item. Advances only quality PRNG; changes no batch/config records. |
| `simulation/inventory_config.py` / `validate_inventory_scenario()` | Resolved loader or resolver; scenario → None | Adds recycler IDs/coordinates, validates disposition/operation consistency and fixed plant-or-recycler destinations. Keeps capacity/range/calendar/route limits. No mutation. |
| `simulation/routing.py` / `dispatch_inventory_trip()` | Engine at fixed departure; scenario/state/trip → None | Keeps released-request input schema free of quality/operations. Selects matching disposition by release/ID within payload; records destination-specific assignments. |
| `simulation/transport.py` / `delivery_site()` | Engine when scheduling inbound leg; scenario/destination ID → site record | Looks up configured plant/recycler coordinates; no mutation. |
| `simulation/transport.py` / `accept_recycling_products()` | Engine immediately after recycler arrival; scenario/state/AV ID → None | Checks eligible actual loads/commitment, removes each item, records terminal recycled state, actual destination, timestamp and acceptance event. Zero service time. |
| `simulation/transport.py` / `retry_inventory_admissions()` | Existing settler; scenario/state → changed flag | Adds a guard requiring remanufacturable load committed to this plant before removal from payload. Existing priority/capacity/partial-unload behavior remains. |
| `simulation/inventory_engine.py` / `run_inventory_simulation()` | Runner; resolved scenario → final state | Uses fixed trip's actual delivery site for inbound movement; accepts recycler loads on arrival, otherwise invokes unchanged plant waiting/settling. Clock/horizon rules remain. |
| `simulation/inventory_engine.py` / `check_inventory_state()` | Engine after settling; scenario/state → None | Includes recycled products as terminal locations in conservation; capacity/payload checks unchanged. No mutation. |
| `simulation/inventory_engine.py` / `inventory_summary()` | Runner; final state → summary | Counts recycled separately and subtracts both terminal dispositions from unfinished; preserves distance/wait calculations. No mutation. |

Existing helpers on the path: `load_scenario()`/`validate_scenario()` read and
validate frozen inputs; `build_inventory_state()` creates empty runtime records;
`record_event()` appends sequenced transitions; `depart_inventory_leg()` /
`arrive_inventory_leg()` move vehicles; `collect_inventory_products()` picks up
assigned released stock; plant/settling functions handle remanufacturing;
`write_json()` writes artifacts. Their bodies are unchanged and explained in
`checkpoint_1_review.md` and `checkpoint_3a_review.md`.

## Every new or changed independent-verifier function

All are in `verify_simulation.py`; none imports simulation/generation/routing
code. They use saved inputs and their own reconstructed state.

| Function/method | Caller; receives → returns | State/files and checks |
| --- | --- | --- |
| `validate_inventory_inputs()` | Supported-format dispatcher; scenario → None | Independently recognizes recycler sites/coordinates, consistent dispositions and fixed destination references, while preserving inventory constraints. No mutation. |
| `audit_weighted_index()` | Seed audit; local PRNG/weights → sampled index | Independent weighted-CDF implementation, advances only audit PRNG by one uniform. No simulation state/files. |
| `audit_seeded_generation()` | Coordinator for version 2; scenario/findings → None | Uses generation markers or seeded provenance to require complete evidence, independently derives named streams and reconstructs batches/products. Appends mismatch findings; missing/unsupported evidence raises EvidenceProblem. Does not trust recorded stream seeds to generate expected inputs. |
| `InventoryAudit.__init__()` | Coordinator; scenario/findings → audit object | Adds plant/recycler site lookup to existing independent empty state and tracking. |
| `InventoryAudit.check_physical_state()` | Existing clock/horizon checks; event → None | Counts recycled products as terminal for exclusive conservation. Only findings may change. |
| `InventoryAudit.capture_dispatch()` | Event handler at first trip action; event/AV ID → None | Independently filters selected requests by fixed destination's disposition, keeping the actual boundary request schema quality-unaware. Records expected decisions/follow-ups. |
| `InventoryAudit.consume_event()` | Replay; observed event → None | Checks disposition before plant/recycler admission; validates destination-specific assignments/geometry, recycler eligibility/location/onboard ownership and mandatory immediate acceptance. Advances own state and milestones only after checks pass. |
| `InventoryAudit.reconcile()` | Coordinator after replay; saved evidence → computed summary | Derives recycled and unfinished counts and compares complete final state, totals and dispatch records. Only own state/findings change. |
| `verify_run()` | Standalone CLI; saved folder → report | Runs independent seed audit plus physical replay, retains byte-level fingerprint, report/exit precedence and updates declared scope. Writes only verification.json/txt via unchanged writer. |

Existing parser/comparison/report helpers retain their contracts from
`checkpoint_2_review.md`. The checker reconstructs only when evidence is
supported; a proved violation outranks incomplete findings. Statistical
validation, finite range, additional policies, experiments and visualization
remain outside this checkpoint.

## Every new test and helper

All methods are in `tests/test_return_generation.py`. unittest calls `setUp()`
before each test; tests receive only self and return None or raise assertions.
All generated/mutated files are temporary and cleaned automatically; committed
fixture bytes remain unchanged.

| Method | Caller; receives/returns; changes and checks |
| --- | --- |
| `setUp()` | unittest; self → None. Allocates scratch directory, registers cleanup and initializes case numbering. |
| `run_configuration()` | Tests; config path/local record, seed default 42 → output path. Writes local configs when necessary and runs the real seeded CLI in a subprocess; asserts success. |
| `verify_directory()` | Tests; saved folder/expected exit → parsed report. Runs standalone verifier, checks process/report exit, reads JSON. Writes only temporary reports through verifier. |
| `test_repeat_and_saved_replay_are_identical()` | For both configs, generates twice and replays frozen input; compares scenario/event/decision/summary/final-state bytes and verifies original/replay. |
| `test_recycling_switch_preserves_batches_and_filters_before_sampling()` | Compares all nine batch records and fixed seed-42 size vector, checks 14 items in both, no disabled QR and enabled 7 acceptances; verifies both. |
| `test_quality_weights_do_not_change_arrival_stream()` | Overrides QR weight to zero; compares batch records/provenance and distinct stream seeds, checks all-reman products and verifies. |
| `test_fixed_batches_and_zero_arrivals()` | Tests fixed batch size 2, then zero rate/no products, independently verifies both. |
| `test_invalid_sampling_settings_are_rejected()` | Mutates local rate/batch/weights/routes, including no positive remanufacturable mass when disabled; expects validation errors. No output runs. |
| `test_cli_rejects_seed_overrides_and_missing_seed()` | Exercises missing config seed, conflicting source arguments and resolved seed override; checks exit 2 and no invalid output directory. |
| `test_remanufacturable_product_cannot_be_accepted_by_recycler()` | Changes one copied acceptance's item ID to a remanufacturable item; checks disposition rule, expected/observed values and event sequence/ID. |
| `test_dispatch_inputs_exclude_individual_quality_and_operations()` | Checks exact request fields and destination-compatible selections, then injects operations into saved boundary input and requires FAIL. |
| `test_horizon_retains_recycler_bound_payload()` | Stops at 6 before recycler arrival; verifies seven recycling items remain onboard, zero recycled and unfinished state retained. |
| `test_generation_evidence_is_audited_independently()` | Forges batch size and updates scenario hash; requires independent generation.arrival_batches violation, proving hash agreement alone does not validate generation evidence. |
| `test_missing_generation_evidence_is_incomplete()` | Removes batches with matching updated hash, then all generation settings; seeded provenance still requires evidence and returns exit 2. |
| `test_saved_generated_fixture_has_lf_and_verifies_in_isolation()` | Checks frozen LF bytes/hash, copies only verifier and fixture, runs isolated Python outside repo, and checks input bytes preserved. |
| `test_distinct_seeds_change_generated_streams()` | Generates seeds 42/43, checks different batches/products/stream seeds and verifies seed-43 run. |
| `test_multiple_recyclers_and_all_recycling_quality()` | Adds RC2, targets it, sets QM weight zero; checks all 14 recycled at RC2 with empty plant load and verifies. |

## Changed files

- `.gitattributes`, `README.md`.
- New `configs/checkpoint_3b_recycling.json`, `configs/checkpoint_3b_no_recycling.json`.
- `simulation/__main__.py`, `inventory_config.py`, `inventory_engine.py`,
  `routing.py`, `runner.py`, `transport.py`.
- New `simulation/return_generation.py`.
- `verify_simulation.py`.
- New `tests/test_return_generation.py`.
- New six-file fixture `tests/fixtures/checkpoint_3b/`: scenario.json,
  run_manifest.json, events.jsonl, dispatch_decisions.jsonl, summary.json,
  final_state.json. This is intentionally frozen test evidence.
- New `docs/checkpoint_3b_review.md`.

No original test files or old fixtures changed. Generated demonstrations,
virtual environment and scratch files remain ignored. Stopped after checkpoint
3b for review.
