# Thesis simulation

A discrete-event simulation of return collection, autonomous vehicle transport and remanufacturing. The included pilot has **3 collection points, 3 vehicles and 3 plants with 3 workstations each**.

A seed determines generated site coordinates, return arrivals, product qualities and workstation availability schedules. Results are saved locally; the viewer can replay them afterward without rerunning the simulation.

## 1. Download and open the project

Clone this repository, then open the folder in VS Code:

```powershell
git clone https://github.com/remgelin-debug/Thesis-simulation-V2.0.git
cd Thesis-simulation-V2.0
code .
```

Alternatively, select **Code > Download ZIP** on GitHub, extract it, and open the extracted folder. Run all commands below from the folder containing `simulation`, `tools` and `configs`.

## 2. Set up Python

Use **Python 3.10 or newer**. Simulation, verification and KPI export need only Python's standard library. The graphical viewer also needs Matplotlib.

### Windows: VS Code PowerShell

Create a local environment and select its interpreter:

```powershell
py -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed' }

$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $thesisPython --version
```

Install the optional viewer dependency:

```powershell
& $thesisPython -m pip install -r .\requirements-visualization.txt
if ($LASTEXITCODE -ne 0) { throw 'Viewer installation failed' }
```

You do not need to activate the environment: every command explicitly uses its Python executable. In a new terminal, restore the variable:

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
```

If you already have a working Anaconda interpreter, you can use it instead:

```powershell
$thesisPython = Join-Path $env:USERPROFILE 'anaconda3\python.exe'
& $thesisPython --version
```

Install viewer dependencies using the same interpreter that will open the viewer.

### Linux or macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-visualization.txt
```

Use `.venv/bin/python` in place of `& $thesisPython` in the commands below, and use forward slashes in paths. The repeated-run examples use PowerShell syntax.

On Debian/Ubuntu, if creating the environment fails, install the distribution's `python3-venv` package. If the desktop viewer needs Tk support, install `python3-tk`. Install Python packages inside the environment rather than into the system Python.

## 3. Run your first simulation

The following PowerShell commands create a unique output folder, run the pilot with seed 42, and independently verify the saved results:

```powershell
$run = Join-Path .\results ("pilot-42-" + [guid]::NewGuid().ToString('N'))

& $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed 42 --policy dynamic_quality_aware --output $run
if ($LASTEXITCODE -ne 0) { throw 'Simulation failed' }

& $thesisPython -m tools.verify $run
if ($LASTEXITCODE -ne 0) { throw 'Verification failed; inspect verification.txt' }
```

The simulation prints a summary of generated, remanufactured, recycled and unfinished products, travel distance and waiting totals. Verification should print **PASS (exit 0)**.

- Change `--seed 42` to generate another scenario.
- Use a fresh output folder for each run. Existing run folders are refused to protect results.
- The same configuration, seed and policy should reproduce the same simulation.
- The pilot uses a horizon of 40 model-time units. Products unfinished at that horizon remain unfinished.
- The generated scenario is saved in `scenario.json`; it contains the exact coordinates, products and workstation windows used.

The pilot has coordinate generation enabled. Its configured coordinate placeholders are replaced by seeded positions within the declared bounds.

## 4. Open the visualization

View the run created above:

```powershell
& $thesisPython -m tools.viewer $run
```

To view a run in a later terminal session, supply its saved folder path:

```powershell
& $thesisPython -m tools.viewer .\results\YOUR-RUN-FOLDER
```

Useful options:

```powershell
# Open at a simulation time:
& $thesisPython -m tools.viewer $run --time 10

# Open immediately after an exact event sequence:
& $thesisPython -m tools.viewer $run --event 20

# Start with a different playback speed:
& $thesisPython -m tools.viewer $run --speed 2

# Save a PNG without opening a desktop window:
& $thesisPython -m tools.viewer $run --time 10 --render (Join-Path $run 'preview.png')
```

Choose an event number that exists in that run. `--time` and `--event` are alternative starting positions.

The viewer starts paused. Controls include Play/Pause, Previous event, Next event, Restart, time seeking and event seeking. Space toggles playback; Left/B and Right/N step backward and forward when a text box is not active.

The display shows vehicles, loads, remaining product operations, plant inventory, reserved slots and workstation schedules. Translucent processing overlays show work performed through the current replay position.

**Time seeking** includes every event through the chosen timestamp. **Event seeking** stops after one exact record, allowing inspection between events with the same time. Playback changes the display only.

To open the viewer automatically after a new simulation, add `--visualize` to the simulation command.

## 5. Compare the two dynamic policies fairly

The network pilot supports:

| Policy | Selection information |
| --- | --- |
| `dynamic_quality_unaware` | Transport information, vehicle constraints and product disposition; no processing forecasts. |
| `dynamic_quality_aware` | Also uses required operations and plant-processing forecasts when selecting routes. |

Use the saved scenario from one run for the other policy. This guarantees identical resolved inputs, even if generation settings are changed afterward:

```powershell
$unaware = Join-Path .\results ("pilot-42-unaware-" + [guid]::NewGuid().ToString('N'))

& $thesisPython -m simulation --resolved-scenario (Join-Path $run 'scenario.json') --policy dynamic_quality_unaware --output $unaware
if ($LASTEXITCODE -ne 0) { throw 'Unaware simulation failed' }

& $thesisPython -m tools.verify $unaware
if ($LASTEXITCODE -ne 0) { throw 'Unaware verification failed' }

Get-Content (Join-Path $run 'summary.json')
Get-Content (Join-Path $unaware 'summary.json')
```

Use `--config` with `--seed` to generate inputs. Use `--resolved-scenario` without `--seed` to execute saved inputs again. Keep the policy flag explicit when repeating a run with a policy override.

Static quality-aware and quality-unaware policies are also supported for single-plant **format-two** inputs containing fixed trips. They cannot be used with the included **format-three** network pilot.

## 6. Run 20 paired replications

This loop runs seeds 1–20. For each seed, it generates inputs once, executes both dynamic policies on those exact inputs and verifies both runs.

It performs **40 simulations sequentially**, then exports one CSV containing all 40 runs:

```powershell
$batchRoot = Join-Path .\results ("paired-20-" + [guid]::NewGuid().ToString('N'))

foreach ($seed in 1..20) {
    $awareRun = Join-Path $batchRoot "seed-$seed-aware"
    $unawareRun = Join-Path $batchRoot "seed-$seed-unaware"

    Write-Host "Seed $seed"

    & $thesisPython -m simulation --config .\configs\three_site_pilot.json --seed $seed --policy dynamic_quality_aware --output $awareRun
    if ($LASTEXITCODE -ne 0) { throw "Simulation failed: $awareRun" }

    & $thesisPython -m tools.verify $awareRun
    if ($LASTEXITCODE -ne 0) { throw "Verification failed: $awareRun" }

    & $thesisPython -m simulation --resolved-scenario (Join-Path $awareRun 'scenario.json') --policy dynamic_quality_unaware --output $unawareRun
    if ($LASTEXITCODE -ne 0) { throw "Simulation failed: $unawareRun" }

    & $thesisPython -m tools.verify $unawareRun
    if ($LASTEXITCODE -ne 0) { throw "Verification failed: $unawareRun" }
}

& $thesisPython -m tools.kpis $batchRoot --output (Join-Path $batchRoot 'kpis.csv')
if ($LASTEXITCODE -ne 0) { throw 'KPI export failed or skipped runs' }
```

Change `1..20` for another seed range. For 20 simulations of just one policy, keep only the aware simulation and verification commands inside the loop.

The separate experiment manager currently accepts format-two generation inputs only; use this loop for the network pilot.

## 7. Export results for Excel

Export one run:

```powershell
& $thesisPython -m tools.kpis $run --output (Join-Path $run 'kpis.csv')
```

Export all runs beneath a batch folder:

```powershell
& $thesisPython -m tools.kpis $batchRoot --output (Join-Path $batchRoot 'kpis.csv')
```

The CSV contains one row per readable completed run, including:

- Seed, policy, verification status and product counts.
- Total AV travel time, distance and unloading wait.
- Collection-point waiting and mean inventory.
- Plant inventory waiting, mean/peak occupied slots and capacity use.
- Plant busy percentages and workstation utilization.
- Per-plant inventory and utilization measurements.

**Plant busy percentage** measures time with at least one active workstation, divided by the full horizon. **Workstation utilization of available time** compares processing time with scheduled available workstation time. Occupied inventory slots include reserved slots for intermediate processing.

The exporter reads existing verification labels; it does not run verification itself. Verify first. Undefined ratios appear as blank cells, and unreadable or incomplete runs are reported as skipped.

The CSV uses UTF-8 with a BOM for Excel. If Excel opens it in one column, use **Data > From Text/CSV** and select a comma delimiter. AV travel time is summed across vehicles, so it can exceed the run horizon.

## 8. Change the scenario

Edit `configs/three_site_pilot.json`, or save a separate configuration under `configs/` and use that path with `--config`.

| Setting | Where to change it |
| --- | --- |
| Number of collection points | Add/remove records in `collection_points`. |
| Return intensity and batch sizes | Each point's `generation` settings. |
| Quality mix and required operations | `quality_classes` and point-specific `quality_weights`. |
| Number of plants | Add/remove records in `plants`. |
| Plant inventory capacity | Each plant's `inventory_capacity`; `null` means unlimited. |
| Workstations and processing durations | Each plant's `workstations` list. |
| Number of vehicles | Add/remove records in `vehicles`. |
| Vehicle settings | `initial_site`, `speed`, `payload_capacity`, `maximum_range`; `null` range means unlimited. |
| Random layout bounds | `coordinate_generation`. Omit this section to use declared fixed coordinates. |
| Availability windows | `schedule_generation`, with supported workstation overrides or explicit `windows`. |
| Simulation end time | `horizon`. Also adjust generation end times where appropriate. |

Use unique entity IDs and valid references. A vehicle's starting site must be a configured plant or recycler. Workstation IDs and operation IDs must be unique within each plant; different plants may reuse workstation IDs.

Every required product operation must be supported at **every plant**. Adding an unused operation does not make products visit it: update their operation sequences too. Multiple parallel workstations with the same operation ID in one plant are not supported.

Keep all times and distances in consistent model units. The seed is supplied at execution, not entered into the generation configuration. Changing a configuration does not modify already-saved runs.

## 9. Understand the saved files

| File | Contents |
| --- | --- |
| `scenario.json` | Exact resolved inputs: positions, releases, required operations, schedules and generation provenance. |
| `run_manifest.json` | Selected policy, input hash and run completion status. |
| `events.jsonl` | Physical events, one JSON record per line, in sequence order. |
| `dispatch_decisions.jsonl` | Routing observations, candidates, predictions and selections. |
| `summary.json` | Main counts, distance and waiting totals. |
| `final_state.json` | Product, vehicle and plant states at the horizon. |
| `verification.json` / `verification.txt` | Independent verification reports, created by `tools.verify`. |
| `kpis.csv` | Optional KPI export created by `tools.kpis`. |

Verification exits are **0 = PASS**, **1 = FAIL**, and **2 = INCOMPLETE**. Inspect `verification.txt` when verification is not PASS.

Do not edit saved evidence, including reformatting `scenario.json`: its exact bytes are hashed. Make a new configuration and run instead.

Generated results are local and ignored by Git. They are not included when another person clones this repository.

## 10. Troubleshooting and checks

| Problem | What to do |
| --- | --- |
| Windows says Python was not found | Use the explicit `$thesisPython` path. Use `py` to create the environment, or point to an installed interpreter. |
| Viewer dependencies are missing | Install `requirements-visualization.txt` with the same interpreter used for the viewer. |
| Linux reports an externally managed environment | Create `.venv` and install inside it; do not install into system Python. |
| No graphical window opens | Use a local desktop session and an interactive Matplotlib backend. If Tk is installed, try `$env:MPLBACKEND = 'TkAgg'` in PowerShell. On a server, use `--render` instead. |
| Output folder already exists | Choose another run folder; the program intentionally refuses overwriting it. |
| No module named simulation/tools | Run commands from the repository root. |
| KPI export reports skipped runs | Inspect its error messages and each run's manifest and verification report. |
| CSV cannot be overwritten | Close it in Excel before exporting again. |

Run the automated tests:

```powershell
& $thesisPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
```

For code responsibilities and execution details, see [the architecture guide](docs/architecture.md).

### Folder guide

| Folder | Purpose |
| --- | --- |
| `configs/` | Editable scenario configurations. |
| `simulation/` | Generation, routing, predictions and physical execution. |
| `tools/` | Saved-run verification, viewing, KPI export and experiments. |
| `results/` | Runs and exports created locally. |
| `tests/` | Automated checks and their input data. |
| `docs/` | Further technical documentation. |
