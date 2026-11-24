# Thesis simulation

A seeded discrete-event remanufacturing simulation with four routing policies,
independent saved-run verification, replay, experiments and Excel-ready KPIs.
The simulation uses one inventory execution loop for fixed routes, dynamic routes and
private dynamic forecasts. Routing selects decisions, forecasting predicts processing,
and the engine applies decisions and executes physical events. The
[architecture guide](docs/architecture.md) describes those interfaces and their ownership.

The confirmed V8 cleanup shares adjacency-only calendar merging, canonical input
serialization and physical conservation/payload checks. It also narrows discarded
verifier copying and shares matching test CLI assertions. Simulation artifacts,
independent diagnostics and all supported commands retain their existing contracts.
S08's viewer cache remains deferred pending measurement; D1 failure-evidence
retention is outside this cleanup.

## Supported formats and retired format one

Format **2** remains the single-plant inventory model. It supports both static
policies and dynamic execution through a `--policy` override. Format **3** remains
the network model and supports both dynamic policies. Single-product scenarios
use a one-item `products` list in either retained format. Version numbers, saved
schemas and the three-site configuration are unchanged. Static network routing
is not supported.

The original format-one scalar engine is retired. It is not automatically
converted. Simulation rejects it before creating an output folder. Verification
of historical format-one results writes fresh **INCOMPLETE / exit 2** reports;
a separately proved contradiction, such as a wrong scenario hash, still gives
**FAIL / exit 1**. Replay and the viewer reject it with a clear error. KPI export
names and skips unsupported runs, keeps supported rows in mixed batches, and
returns its existing nonzero skip result. Experiment refresh excludes unsupported
members from verified comparisons while retaining attempts.

Historical fixtures and review notes remain unchanged. To execute or inspect
format-one results with the previous implementation, use
`simulation-v8-readable-code-layout` at
`1753c587da13442914743da841d867e712b6cdb1` in a separate checkout or worktree.
The retrieval helper near the end of this guide safely switches branches.
These rules concern **scenario** versions; experiment manifest version 1 and
single-site generation provenance version 1 are separate contracts and remain.

## Layout

- `configs/`: current user configuration, `three_site_pilot.json`.
- `simulation/`: consolidated generator, typed state records, physical rules, routing and execution.
- `tools/`: independent verification, replay/viewer, experiments and KPI export.
- `results/`: **generated locally**, ignored by Git. Programs create output parents.
- `tests/`: regression tests, immutable audited fixtures and historical test inputs.
- `docs/architecture.md`: module responsibilities, data structures and call sequence.

The pilot has three collection points, three AVs and three remanufacturing plants
with three workstations each. Its existing return/quality generation, independent
coordinate streams in `[0, 8]` on each axis, fleet/facility settings and generated
workstation calendars are retained. Initial calendar delay is `[0, 0]`.

## Windows setup

Run from the repository root in VS Code PowerShell. Python 3.10+ is required.
Simulation, verification, experiments, KPI export and non-graphical replay use
only the standard library. Install Matplotlib only for viewing or rendering.
If `$thesisPython` already points to your working interpreter, reuse it and
check `& $thesisPython --version`. For a new local environment, use the setup below.

```powershell
py -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed' }
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $thesisPython --version
if ($LASTEXITCODE -ne 0) { throw 'Interpreter check failed' }
# Optional viewer dependency:
& $thesisPython -m pip install -r .\requirements-visualization.txt
if ($LASTEXITCODE -ne 0) { throw 'Viewer dependency installation failed' }
```

## Run and verify

Choose a new output folder for each simulation; existing run folders are refused
to protect saved evidence. `--config` requires a seed; `--resolved-scenario`
replays saved inputs without sampling and does not accept a seed override.

```powershell
$demoRoot = Join-Path .\results ("pilot-demo-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$aware42 = Join-Path $demoRoot 'seed-42-aware'
$aware43 = Join-Path $demoRoot 'seed-43-aware'
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 42 --policy dynamic_quality_aware --output $aware42
if ($LASTEXITCODE -ne 0) { throw 'Seed-42 simulation failed' }
& $thesisPython -m tools.verify $aware42
if ($LASTEXITCODE -ne 0) { throw 'Seed-42 verification failed' }
# A different seed generates a different layout, returns/qualities and calendars:
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 43 --policy dynamic_quality_aware --output $aware43
if ($LASTEXITCODE -ne 0) { throw 'Seed-43 simulation failed' }
& $thesisPython -m tools.verify $aware43
if ($LASTEXITCODE -ne 0) { throw 'Seed-43 verification failed' }
```

Open `scenario.json` for all resolved coordinates, products, windows and seed
provenance. `run_manifest.json` records its **byte-for-byte SHA-256** and selected
policy. `events.jsonl` records sequenced physical transitions;
`dispatch_decisions.jsonl` records candidates, forecasts, scores and selections.
`summary.json` and `final_state.json` describe the horizon. The verifier writes
`verification.json` and `verification.txt`, returning PASS/0, FAIL/1 or
INCOMPLETE/2. It independently reconstructs the run rather than calling engines.
Do not edit/reformat a saved scenario if you want its hash to remain valid.

This branch retains the delivery-service fix and corrects audit findings
F2/F3. Discrete seeds, counts, indices and revisions compare exactly; physical
values retain the existing tolerance. Malformed nested evidence produces a fresh
INCOMPLETE report instead of leaving a stale PASS. Any independently proved
violation takes precedence and produces FAIL. Existing saved valid simulations
can be re-verified directly; no simulation inputs or outputs need regenerating.
The [verifier evidence notes](docs/architecture.md#malformed-evidence-and-fresh-reports-f3)
record the boundaries and counterexamples. F4/F5 are corrected by always-active
physical guards and derived-arithmetic checks.
F6 now reuses execution-owned calendars without changing predictions or evidence;
full forecasts remain expensive. F7 constructs finite-range reset variants
incrementally while retaining every candidate and complete decision evidence.
F8 indexes completed processing history once instead of copying it into every
replay checkpoint. Complete evidence and viewer behaviour are preserved; full
forecasts, retained decision logs and copied visible replay history still cost
time/memory. D1 was not implemented. See the
[measurements and limits](docs/architecture.md#controlled-optimization-measurements).
See the [calendar ownership notes](docs/architecture.md#execution-owned-prepared-calendars-f6).
See [physical guard contracts](docs/architecture.md#always-active-physical-guards-f4)
and [numerical representability](docs/architecture.md#representable-physical-arithmetic-f5).
Ordinary Python and optimized Python enforce the same rules. Unrepresentable
positive travel/processing raises a clear `ValueError` subclass; zero-distance
visits remain immediate. Valid ordinary runs retain their saved artifacts.
Existing saved evidence can be independently re-verified to detect impossible
zero-time operations; physical comparison tolerances and exact seed checks remain unchanged.

For a practical refresh check, copy the verified `$aware42` run to a unique
temporary folder and make only its manifest provenance malformed. The scenario
bytes/hash remain unchanged. Python performs the JSON edit so large seed integers
are preserved on Windows PowerShell as well as newer PowerShell versions.

```powershell
$badEvidence = Join-Path ([System.IO.Path]::GetTempPath()) ("thesis-evidence-" + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath $aware42 -Destination $badEvidence -Recurse -ErrorAction Stop
@'
import json, sys
from pathlib import Path
path = Path(sys.argv[1]) / 'run_manifest.json'
manifest = json.loads(path.read_bytes())
manifest['seed_provenance'] = []
path.write_text(json.dumps(manifest) + '\n', encoding='utf-8', newline='\n')
'@ | & $thesisPython - $badEvidence
if ($LASTEXITCODE -ne 0) { throw 'Temporary evidence edit failed' }
& $thesisPython -m tools.verify $badEvidence
if ($LASTEXITCODE -ne 2) { throw 'Expected INCOMPLETE exit 2' }
Get-Content (Join-Path $badEvidence 'verification.txt')
```

The configuration declares generation settings. The resolved scenario contains
the generated products, coordinates, calendars and provenance. Its future returns
allow repeatable execution. Dispatchers receive released returns at the decision
time. Unaware observations expose transport facts and disposition. Aware
observations also expose operations and permitted current physical state.

To rerun the same resolved inputs under the same policy, use a fresh folder and
omit `--seed`. This executes the simulation again using those saved inputs.

```powershell
$repeat42 = Join-Path $demoRoot 'seed-42-repeat'
& $thesisPython -m simulation --resolved-scenario (Join-Path $aware42 'scenario.json') --policy dynamic_quality_aware --output $repeat42
if ($LASTEXITCODE -ne 0) { throw 'Resolved rerun failed' }
& $thesisPython -m tools.verify $repeat42
if ($LASTEXITCODE -ne 0) { throw 'Resolved rerun verification failed' }
```

All four policies remain available: `static_quality_unaware`,
`static_quality_aware`, `dynamic_quality_unaware`, `dynamic_quality_aware`.
The network pilot uses the dynamic policies. Fixed policies retain the supported
single-plant format-two inputs/trips. Format one is retired.
Tests exercise both retained formats without keeping obsolete user configurations.

This branch fixes the stage-4/stage-5 seed-54 delivery-service defect. Dynamic
facilities accept only products authorized at the current route stop. Products
for later visits remain onboard, including during blocked-unloading retries.
Static final-trip inventory service is unchanged. Earlier affected experiment
results require rerunning; a prior successful run or PASS report does not establish
that it was unaffected. The [architecture guide](docs/architecture.md#dynamic-facility-service-and-the-f1-correction)
records the rule, diagnostic and regression evidence. Seeds 42–61 now complete
and pass independent verification under both dynamic policies.

## Run 20 paired seeded replications and export one batch CSV

This loop freezes one scenario per seed, reuses it for the other dynamic policy,
verifies each run, and exports **one `kpis.csv` per batch**. Use a fresh batch name
when repeating the loop. The tested range below is seeds 42–61, producing 40 runs
and 40 verification calls.
Policy overrides preserve the saved scenario bytes. Run names, seeds and policies
identify paired inputs in the CSV.

```powershell
$batchRoot = Join-Path .\results ("pilot-20-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
foreach ($seed in 42..61) {
    $awareRun = Join-Path $batchRoot "seed-$seed-aware"
    $unawareRun = Join-Path $batchRoot "seed-$seed-unaware"
    & $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed $seed --policy dynamic_quality_aware --output $awareRun
    if ($LASTEXITCODE -ne 0) { throw "Simulation failed: $awareRun" }
    & $thesisPython -m tools.verify $awareRun
    if ($LASTEXITCODE -ne 0) { throw "Verification failed: $awareRun" }
    & $thesisPython -m simulation --resolved-scenario (Join-Path $awareRun 'scenario.json') --policy dynamic_quality_unaware --output $unawareRun
    if ($LASTEXITCODE -ne 0) { throw "Simulation failed: $unawareRun" }
    & $thesisPython -m tools.verify $unawareRun
    if ($LASTEXITCODE -ne 0) { throw "Verification failed: $unawareRun" }
}
# Add this AFTER your existing simulation and verification loop:
& $thesisPython -m tools.kpis $batchRoot --output (Join-Path $batchRoot 'kpis.csv')
if ($LASTEXITCODE -ne 0) { throw 'KPI export failed or skipped runs' }
# The original exporter command is also supported:
# & $thesisPython .\export_simulation_kpis.py $batchRoot --output (Join-Path $batchRoot 'kpis.csv')
```

The exporter reads saved artifacts, reports skipped runs and uses `NOT_VERIFIED`
when a verifier report is absent. The UTF-8/BOM CSV opens in Excel and contains
one row per completed run. Its time metrics integrate events through the horizon,
including reroutes/partial legs, waiting stock, reserved slots and processing.
Undefined ratios are blank. KPI export does not itself verify runs.
It preserves the saved verification label. A fresh verifier invocation checks
evidence after subsequent modifications. A PASS concerns the supported rules
checked against available evidence.

## Visualize or replay

```powershell
& $thesisPython -m tools.viewer $aware42
& $thesisPython -m tools.viewer $aware42 --event 49
& $thesisPython -m tools.viewer $aware42 --time 5
# Optional viewing immediately after a new simulation:
$visualRun = Join-Path $demoRoot 'seed-44-visual'
& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 44 --policy dynamic_quality_aware --output $visualRun --visualize
if ($LASTEXITCODE -ne 0) { throw 'Visualized run failed' }
# Render without a desktop (Matplotlib still required):
& $thesisPython -m tools.viewer $aware42 --time 10 --render (Join-Path $aware42 'viewer.png')
```

Playback starts paused. Use Play/Pause, Previous event, Next event, Restart,
Seek time and Seek event. Previous/Next move exactly one **event sequence**, so
same-time completion/start transitions remain inspectable; previous from the
first record restores the initial state. Time seeks include all events through
that timestamp. The clock and AV motion interpolate recorded simulation time.
The details show waiting inventory, held slots, onboard parts and remaining
operations. The nine timetable rows use saved calendars, with a moving red
marker and translucent orange processing history up to the replay position.
Display replay reads recorded events and interpolates recorded travel times.
It uses saved coordinates and calendars. Viewing writes a PNG only for an explicit
render request.

## Experiments and tests

The existing experiment manager accepts single-site format-two generation
conditions and defaults to all four policies. Its scope is unchanged; use the
loop above for multi-site pilot batches. The historical condition below is a
**test input**, not another current user configuration.

```powershell
$experimentRoot = Join-Path .\results ("experiment-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $thesisPython -m tools.experiments --config .\tests\inputs\checkpoint_5.json --replications 2 --seed 2026 --output $experimentRoot
if ($LASTEXITCODE -ne 0) { throw 'Experiment failed or has exclusions' }
& $thesisPython -m tools.experiments --output $experimentRoot --refresh-verification
if ($LASTEXITCODE -ne 0) { throw 'Refresh found failed or incomplete comparisons' }
& $thesisPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Test suite failed' }
```

Each condition/replication resolves inputs once, retains IDs/seeds and exact
rerun commands in `experiment_manifest.json`, and runs each policy in its own
folder. Failed or incomplete runs and their artifacts remain visible; verified
comparisons exclude them and identify missing policies. `results.csv` preserves
replication IDs; grouped summaries include only eligible verified results.

Root compatibility commands still work: `python verify_simulation.py RUN`,
`python export_simulation_kpis.py RUN_OR_BATCH --output FILE`, and
`python run_experiments.py ...`.
Use the `tools` module commands above for new scripts. These wrappers preserve
CLI arguments. Generation callers now import `simulation.generator`; the internal
`scenario`, `returns` and `schedules` modules have been removed without shims.
Record annotations do not construct/validate/convert dictionaries at runtime;
the current validators still enforce inputs. See the architecture guide for
required/optional record fields, units and live versus forecast ownership. Standalone
copies of `tools/verify.py` and `tools/kpis.py` need only the standard library.

## Retired module paths

Two obsolete compatibility modules have been removed; use their implementations directly:

| Retired entry point | Replacement |
| --- | --- |
| `simulation.fixed_engine` (`simulation/fixed_engine.py`) | Import `dispatch_inventory_trip` and `run_inventory_simulation` from `simulation.engine`; the scalar format-one APIs are retired. |
| `python -m simulation.visualization RUN_FOLDER` (`simulation/visualization.py`) | `python -m tools.viewer RUN_FOLDER`, with the same time/event/speed/render options. |

`python -m simulation ... --visualize` still opens the actual viewer after saving
its run. Python integrations can call `tools.viewer.open_viewer`. The root verifier,
experiment and KPI wrappers remain supported. Earlier commands in `docs/history/`
are historical records; use the current commands above for new runs.

## Retrieve this branch or return to the previous implementation

Format-one retirement starts at readable-layout commit
`1753c587da13442914743da841d867e712b6cdb1`. Retained format-two/three behavior is
compared against that untouched version. Earlier branches and commits remain available. The helper supports single-branch clones and stops
before any pull if fetching or switching fails; it never discards local edits.

```powershell
function Open-ThesisBranch([string]$BranchName) {
    git fetch origin "refs/heads/${BranchName}:refs/remotes/origin/${BranchName}"
    if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
    git show-ref --verify --quiet "refs/heads/$BranchName"
    if ($LASTEXITCODE -eq 0) {
        git switch $BranchName
    } else {
        git switch --create $BranchName --track "origin/$BranchName"
    }
    if ($LASTEXITCODE -ne 0) { throw 'Switch failed; stopping before pull' }
    git pull --ff-only origin $BranchName
    if ($LASTEXITCODE -ne 0) { throw 'Pull failed' }
}
Open-ThesisBranch 'simulation-v8-remove-format-one'
# To return later:
# Open-ThesisBranch 'simulation-v8-readable-code-layout'
```

[Controlled measurements and local before/after commands](docs/architecture.md#local-beforeafter-check)
explain how to use the same interpreter and saved inputs in separate worktrees.
Expect small/no whole-pilot runtime gain from calendar reuse; complete forecasts
and decision evidence remain expensive. Replay-history memory improves most on
runs with many completed operations; seeking still copies visible history.

See [architecture](docs/architecture.md) for the call sequence and
[test inputs](tests/README.md) for fixture provenance. Earlier checkpoint review
notes are retained in `docs/history/` as historical descriptions of the old layout.
