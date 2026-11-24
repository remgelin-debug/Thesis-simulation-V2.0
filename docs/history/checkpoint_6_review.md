# Checkpoint 6: optional Matplotlib single-run viewer

> Historical checkpoint-6 record. Paths and commands below describe that earlier
> version. For current entry points, use README.md and docs/architecture.md.

Created `thesis-checkpoint-6` from checkpoint 5 commit
`594c0fc6dedd480d599f7ccc537e3b6197d91cb1`. Read Sections 7, 8 and 12 of the
supplied specification. Implemented checkpoint 6 only. Source, tests and documentation are maintained on `thesis-checkpoint-6`
for review; engine, policies, independent verifier and experiment runner are unchanged.

## What opens, and where its state comes from

`python -m simulation ... --visualize` runs and saves one normal simulation first,
then opens a **paused replay of that same run**. The controls operate the viewing
clock, not the completed engine. This makes the saved evidence independent of
frame rate, viewer speed, pause duration or window availability. There is no second
simulation inside the viewer. Closing the window returns to the terminal.

`python -m simulation.visualization RUN_FOLDER` opens an existing run directly.
Neither path requires experiments. Both work with legacy format 1, seeded/resolved
format 2, and dynamic format 3, including multiple plants and recycling.

The optional dependency is `matplotlib>=3.8,<4`, declared in
`requirements-visualization.txt`. It is imported only when a viewer is requested.
Headless simulation, experiments and independent verification keep their standard
library dependency boundary. This machine has Matplotlib 3.10.8 and used Agg;
a desktop window was unavailable here. Real mouse/window behavior must be checked
locally, but rendering and the same callbacks were exercised under Agg.

The viewer reads only `scenario.json`, `run_manifest.json`, and `events.jsonl`.
It checks the raw scenario hash, completed manifest status, contiguous event
sequences, finite/ordered event times and known display transitions. These are
reader checks, not a replacement for the independent verifier. It does not use
summary/final state as intermediate animation frames or forecast new events.

`RunReplay` normalizes a private copy of the network and initializes empty display
containers. It then applies recorded releases, assignments, departures, cancellations,
arrivals, pickups, admissions, holds, returns, work starts/completions, resets and
recycler acceptances. A checkpoint copy every 100 records bounds the work needed
for repeated seeks without storing a full copy after every event. Requested frames
are isolated copies, so neither rendering nor navigation changes cached state.

Vehicle positions are interpolated from the **currently active recorded leg**:

```
fraction = clamp((view_time - departure_time) / (arrival_time - departure_time), 0, 1)
position = origin_coordinates + fraction * (target_coordinates - origin_coordinates)
remaining_range = departure_allowance - fraction * recorded_leg_distance
```

A zero-duration leg uses its target coordinate. Cancelled legs are cleared at their
logged cancellation and replaced by the following departure. Horizon-truncated
legs remain partway along the recorded segment. Plant inventory, holds, load,
workstation jobs and product states change at the logged transitions, never via
interpolation or independent routing/plant decisions.

`ReplayController` owns the viewing time, event cursor, pause flag and playback
speed. The GUI timer measures elapsed wall time, multiplies by speed and requests
an event-derived frame. It never advances by a fixed number of simulation events
per rendered frame. Autoplay settles all records through its sampled time;
event stepping preserves individual same-time transitions for inspection.

The map labels every collection point, plant and recycler, available collection
products, AV positions and onboard IDs. Dashed lines show remaining routes.
The panel shows each AV's load/status/range, every plant's waiting inventory,
held slots, waiting-to-unload AVs and active workstation jobs with start/end times.
Idle stations are labelled open/closed from configured availability windows.
Station maps are keyed by plant, so RF1/WS1 and RF2/WS1 remain distinct.

## Controls and exact walkthrough

The viewer always starts paused, at time zero unless a starting time/event is given.

- **Resume / Pause** or **Space**: toggle animation. Speed is simulation units per
  real second, default 1 (`--view-speed` for new runs, `--speed` for replay).
- **Next event** or **Right / N**: pause and apply exactly one further log record.
  Multiple records may have the same simulation time.
- **Time + Enter**: jump to a time and apply all records through it, then pause.
- **Event # + Enter**: stop immediately after that sequence number, then pause.
- **Restart**: pause at time zero after its same-time records.
- Close the window to return to PowerShell. Playback stops at the run horizon.

Keyboard shortcuts are disabled while typing into a seek box. Click the map to
leave the box before using Space/Right/N. Input errors appear in the message area
without changing the last valid state. The seek boxes hold input values; the plot
heading is the current authoritative clock and last-applied sequence number.

Use `configs/checkpoint_4b.json`, policy `dynamic_quality_unaware`, and start with
`--view-time 5`. The initial displayed snapshot is settled through sequence 16.
It has AV1 at `(3,0)`, P1 onboard, remaining range 5, and route CP2→RF1. P2 is
available at CP2 `(2,1)`; RF1 inventory is empty and WS1 idle/open.

| Stop / action | Expected and observed time | Display and comparison record |
|---|---:|---|
| Pause at reroute | 5 | #14 `leg_cancelled` records `(3,0)`, load retained and old ETA 8 cancelled; #15 `route_revised` records CP2→RF1; #16 departs toward CP2 with P1. |
| Click Next event once | 6.414213562373095 | #17 `leg_arrival`: AV1 at CP2, still only P1 onboard. Arrival precedes pickup. |
| Click Next event again | 6.414213562373095 | #18 `pickup` P2: load becomes P1,P2. |
| Step through #19–20 | 6.414213562373095 | Stop completed, then departure from CP2 toward RF1 with both products. |
| Step to #21 | 8.650281539872886 | `leg_arrival` at RF1 `(0,0)`, both still onboard; remaining range 1.3497184601271153. |
| Step to #22 | Same | `range_reset` to 10, before unloading. |
| Step #23 then #24 | Same | `plant_admission` P1 then P2; AV becomes empty, plant waiting inventory temporarily contains both. |
| Step to #25 | Same | `operation_start`: P1 on RF1/WS1, end 9.650281539872886; P2 waits in shared inventory. |
| Step through #26–28 | Same | Facility stop completed, AV availability, batched dispatch; P1 keeps processing. |
| Next event / seek #29 | 9.650281539872886 | `operation_complete` P1: WS1 idle, P2 still waiting; P1 display state is `operation_complete`. |
| Next event #30 | Same | `remanufactured` P1; this follows the completion as a separate record. |
| Next event #31 | Same | P2 starts on RF1/WS1, end 10.650281539872886. |

At an exact completion microstate, `operation_complete` is a temporary display
label while the next terminal/return event has not yet been applied. It is not
a new simulation state or a modified log record. Seeking by **time** to the first
completion settles #29–31 and therefore shows P1 remanufactured and P2 already
working; seek **event 29** to inspect the completion itself.

There is no arrival, delivery or reset at the superseded time 8. At that viewing
time AV1 is interpolated on the CP2→RF1 segment, with both products onboard.
For another hand check, halfway along the diversion at `5 + sqrt(2)/2`, AV1 is at
`(2.5,0.5)` with `5 - sqrt(2)/2` range remaining.

## Observed checks

| Case | Expected | Observed |
|---|---|---|
| Visualization-enabled versus headless core artifacts | Identical bytes | events.jsonl, dispatch_decisions.jsonl, summary.json, final_state.json all identical |
| Saved scenario | Identical raw bytes/hash | Identical |
| Independent verification | Both PASS / exit 0 | Both PASS / exit 0 |
| Total distance | 5 + sqrt(2) + sqrt(5) | 8.650281539872886 in both runs |
| Outputs | 2 remanufactured, 0 unfinished, plant inventory wait 1 | Exactly that in both runs |
| Read-only saved replay | No evidence changes | Every copied evidence file hash unchanged after rendering |
| Tests | 118 existing + 17 new | 135 tests passed, no skips with dependency installed |
| Graphical window | Requires desktop backend | Unavailable here; Agg PNGs and callback/control behavior exercised |

Run manifests have different run IDs because output folders differ. Their input
hash/policy match. The four requested core files are byte-identical, not merely
numerically close. No viewer setting is added to or used by simulation inputs.

Artifacts demonstrated here are under ignored `results/checkpoint-6-review`:
`headless/`, `visual/`, `time-5.png`, and `first-completion.png`. Rendered screenshots
were inspected at time 5 and immediately after event 29. The full test log is
`work/checkpoint-6-all-tests.txt`, also ignored.

## Exact PowerShell setup and commands

Use your existing `$thesisPython`, or set it to the project virtual environment.
Install the optional dependency before running the full visualization tests.
Use fresh run directories.

```powershell
$thesisPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $thesisPython -m pip install -r requirements-visualization.txt

# A desktop Matplotlib backend for the VS Code PowerShell terminal:
& $thesisPython -c "import tkinter; print(tkinter.TkVersion)"
$env:MPLBACKEND = 'TkAgg'

$demo = Join-Path (Get-Location) 'results\checkpoint-6-review'

# Save a new run, then open its viewer already PAUSED at time 5:
& $thesisPython -m simulation --resolved-scenario configs/checkpoint_4b.json --policy dynamic_quality_unaware --output "$demo\visual" --visualize --view-time 5
# Close the viewer when ready; the run files were saved before it opened.

# Replay that saved run without running the simulation again:
& $thesisPython -m simulation.visualization "$demo\visual" --time 5
# Or open immediately after the first workstation completion record:
& $thesisPython -m simulation.visualization "$demo\visual" --event 29

# Headless run of exactly the same source and policy:
& $thesisPython -m simulation --resolved-scenario configs/checkpoint_4b.json --policy dynamic_quality_unaware --output "$demo\headless"

foreach ($file in @('scenario.json','events.jsonl','dispatch_decisions.jsonl','summary.json','final_state.json')) {
    [pscustomobject]@{
        File = $file
        Identical = (Get-FileHash "$demo\visual\$file").Hash -eq (Get-FileHash "$demo\headless\$file").Hash
    }
} # Every row: True

& $thesisPython verify_simulation.py "$demo\visual"
$LASTEXITCODE # 0, PASS
& $thesisPython verify_simulation.py "$demo\headless"
$LASTEXITCODE # 0, PASS

$events = @(Get-Content "$demo\visual\events.jsonl" | ForEach-Object { $_ | ConvertFrom-Json })
$events | Where-Object { $_.sequence -in @(14,15,16,17,18,21,22,23,24,25,29,30,31) } | Format-Table sequence,time,event_type,product_id,vehicle_id,workstation_id
($events | Where-Object sequence -eq 15).new_remaining_stops | ConvertTo-Json -Depth 10

# Explicit headless PNG rendering; --render selects Agg inside this process:
& $thesisPython -m simulation.visualization "$demo\visual" --time 5 --render "$demo\time-5.png"
& $thesisPython -m simulation.visualization "$demo\visual" --event 29 --render "$demo\first-completion.png"
# A new run with visualization enabled can also render without a GUI:
& $thesisPython -m simulation --resolved-scenario configs/checkpoint_4b.json --policy dynamic_quality_unaware --output "$demo\visual-agg" --visualize --view-time 5 --render "$demo\new-run-time-5.png"

& $thesisPython -m unittest discover -s tests -v
# Expected: Ran 135 tests ... OK
& $thesisPython -m unittest discover -s tests -p test_visualization.py -v
# Expected: Ran 17 tests ... OK
```

Python equivalents ran in this Linux environment; PowerShell itself was not
available. The Tk backend needs the Tcl/Tk feature supplied by a desktop Python
installation. If that import fails, enable Tcl/Tk in the Python installer or use
an installed Qt Matplotlib backend. `--render` is the GUI-independent alternative.
A headless backend requested without `--render` reports an actionable error; if
that happens after a new run, its evidence has already been saved and can be
replayed later. PNG export requires a `.png` suffix to avoid overwriting evidence
files such as events.jsonl.

Locally check the actual Tk window opens, Resume animates smoothly, Pause freezes
it, and Space/Right/N work after leaving a seek textbox. Enter time 5 and event
29, and compare the panel against the table above. Mouse/window interactions
were not verified here; the underlying controller, widget callbacks, seek errors,
keyboard focus guard and PNG rendering were tested with Agg.

## Every function added or changed

`Record` is the existing typed dictionary alias; all new functions/methods have
type hints and short docstrings. No existing physical rule is changed.

### Entry and read-only snapshots

| Function / location | Caller; receives → returns | State/files affected |
|---|---|---|
| `main()` / simulation/__main__.py (changed) | Single-run CLI; source/seed/policy/output and optional viewer flags → exit int | Existing run_once saves normal evidence; lazily calls open_viewer afterward. Viewer flags require --visualize, time/event are mutually exclusive. Viewer errors preserve completed output and return 1. |
| `normalize_view_scenario()` / simulation/replay.py | RunReplay initialization; saved scenario → copied list-based view inputs | Converts legacy singular vehicle/product/collection/plant into display lists, preserves all explicit plants. No file/input mutation. |
| `initial_view_state()` / same | RunReplay; normalized scenario → new display dictionary | Initializes unreleased products, parked AVs, empty local inventory/holds/queues/stations. Does not release future products. |
| `apply_view_event()` / same | Initial cache build and snapshot reconstruction; mutable display state/event → None | Applies exactly the recorded transition in sequence, updates event cursor. Moves load/stock/jobs, changes recorded route/leg/reset and product progress; no dispatch, random sampling or plant scheduling. Rejects unknown event kinds. No I/O. |
| `interpolate_vehicles()` / same | snapshot; copied state/requested time → None | Changes only copied AV positions/range and displayed time using active-leg timestamps; clamps fraction and handles zero-duration/truncated legs. |
| `RunReplay.__init__()` / same | open_viewer/tests; run-folder Path → initialized replay instance | Reads the three evidence files, checks raw hash/completed/order, builds sparse in-memory checkpoints. Never writes evidence. |
| `RunReplay.snapshot()` / same | Controller/tests; time and optional applied-event count → isolated state dictionary | Binary-searches times for settled count or honors exact cursor, copies nearest checkpoint, applies remaining records, interpolates. Rejects nonfinite/out-of-horizon times and invalid cursors. Cache/evidence unchanged. |
| `ReplayController.__init__()` / same | open_viewer/tests; replay/start time/speed → initialized controller | Starts paused and requests first snapshot; rejects nonpositive/nonfinite speed. |
| `ReplayController.seek_time()` / same | Time control/tests; simulation time → None | Replaces current display state with all-events-through-time snapshot and pauses. |
| `ReplayController.seek_event()` / same | Event control/step/tests; sequence int → None | Requires 1..event count, requests exact-record microstate at its logged time, pauses. |
| `ReplayController.step_next_event()` / same | Next button/shortcut/tests; no input → None | Pauses and consumes one next sequence; remains stable at end. |
| `ReplayController.toggle_play()` / same | Play button/shortcut/tests; no input → None | Toggles pause unless at horizon; no snapshot/file changes. |
| `ReplayController.advance()` / same | GUI timer/tests; elapsed seconds → None | While running requests time + elapsed×speed, clamps to horizon and pauses there. Negative/nonfinite intervals rejected. Paused elapsed time changes nothing. |

For `snapshot(5)`, binary search includes all time-5 records through #16. Starting
from a copied checkpoint, #14 cancels the old leg, #15 replaces stops, #16 sets the
new leg from `(3,0)` to `(2,1)`. Interpolation at departure has fraction zero.
`seek_event(18)` instead replays through only #18 at 6.414213562373095 and shows
two onboard products before the later same-time departure record has been applied.

### Matplotlib presentation (simulation/visualization.py)

| Function/method | Caller; receives → returns | State/files affected |
|---|---|---|
| `draw_snapshot()` | Viewer redraw; map/detail Axes, scenario, snapshot, policy, pause flag → None | Replaces artists with map, labels, routes, inventory/jobs, clock and event heading. Only Matplotlib artists change; read-only snapshot. |
| `MatplotlibViewer.__init__()` | open_viewer/tests; controller → initialized viewer | Lazily imports Matplotlib/widgets, creates figure/axes/buttons/textboxes, callback connections and a 40ms timer; draws initial paused state. Gives installation guidance on missing dependency. |
| `MatplotlibViewer.redraw()` | Construction and callbacks; no input → None | Reads controller, updates artists/button label/next-record hint, requests canvas redraw. Does not advance time. |
| `MatplotlibViewer.toggle_play()` | Button or Space; optional GUI event → None | Toggles controller and resets wall-time reference, redraws. Paused wall time cannot cause a jump. |
| `MatplotlibViewer.step_next_event()` | Button/Right/N; optional GUI event → None | Calls controller's one-record step and redraws. |
| `MatplotlibViewer.restart()` | Restart button; optional GUI event → None | Pauses at settled time zero and redraws. |
| `MatplotlibViewer.seek_time()` | TextBox submit; text → None | Parses float, calls time seek/redraw; invalid input changes message artist only. |
| `MatplotlibViewer.seek_event()` | TextBox submit; text → None | Parses int, calls exact-event seek/redraw; invalid input leaves state unchanged. |
| `MatplotlibViewer.on_key()` | Canvas key event; event object → None | Ignores shortcuts during textbox typing; routes Space/Right/N to existing control callbacks. |
| `MatplotlibViewer.on_timer()` | Matplotlib timer; no input → None | Measures perf_counter interval, calls controller.advance and redraws running/end state. Does not run simulation or edit logs. |
| `MatplotlibViewer.on_close()` | Canvas close event; optional event → None | Stops the timer, leaves evidence intact. |
| `MatplotlibViewer.show()` | open_viewer; no input → None | Requires desktop backend, starts timer and blocking GUI event loop. Non-GUI canvas gives replay/render guidance. |
| `MatplotlibViewer.save_image()` | open_viewer/tests; PNG Path → None | Validates suffix, creates image parent if needed, saves current figure at 130 dpi. Only explicit PNG path written. |
| `MatplotlibViewer.close()` | open_viewer finally/tests; no input → None | Stops timer and releases figure resources. |
| `open_viewer()` | Existing CLI or replay CLI/tests; run Path, optional time/event/speed/render Path → None | Selects Agg for explicit rendering, loads replay/controller, optionally seeks exact event, creates viewer, shows or saves PNG, always closes resources. No evidence rewrite. |
| `main()` | python -m simulation.visualization; folder/time-or-event/speed/render argv → exit int | Parses existing-folder request; calls open_viewer. Returns 0 after closure/render, 1 with readable load/backend/navigation error. Never starts another simulation. |

### Existing project boundary calls (unchanged)

| Function | Caller; receives → returns; effects |
|---|---|
| `runner.run_once()` / simulation/runner.py | Existing single-run main; source/output, optional seed/policy → summary. Generates or loads inputs, runs existing engine, saves six normal evidence files before viewer call. |
| `config.load_scenario()` / simulation/config.py | run_once for walkthrough; source Path → parsed scenario/raw bytes. Reads/validates resolved input; no rewriting. |
| `dynamic_config.as_dynamic_scenario()` / simulation/dynamic_config.py | run_once; resolved scenario → execution copy with dynamic shape; source bytes untouched. |
| `dynamic_engine.run_dynamic_simulation()` / simulation/dynamic_engine.py | run_once; execution scenario → settled InventoryRunState with events/decisions. Existing 4b dispatch and physical rules unchanged; viewer does not call it. |
| `inventory_engine.inventory_summary()` / simulation/inventory_engine.py | run_once; final state → totals; read only. |
| `runner.write_json()` / simulation/runner.py | run_once; output Path/value → None; writes existing run metadata/state/summary. |
| `verify_run()` / verify_simulation.py | Existing verifier CLI/tests; run-folder Path → independent report; reconstructs evidence and writes verification.json/txt. No Matplotlib/replay dependency added. |

Earlier checkpoint guides describe the unchanged transport/plant/routing call tree.
The viewer path begins only after run_once has returned.

### Every test and helper (tests/test_visualization.py)

unittest calls both setUp methods and each test without arguments; all tests return
None. All generated evidence/corruption/PNG outputs use cleaned temporary folders.
The immutable repository fixtures are never rewritten. Five rendering tests skip
only when optional Matplotlib is absent; all 17 ran here with it installed.

| Method/helper | Caller, input/return, effects and check |
|---|---|
| `ReplayTests.setUp()` | unittest → None; allocates scratch/cleanup and reads immutable 4b replay. |
| `test_time_five_reroute_and_cancelled_arrival()` | Pure snapshots; checks (3,0), P1, range 5, new target and no arrival/reset at 8. |
| `test_exact_pickup_arrival_and_first_completion_records()` | Controller calls; checks records 17/18, 21, 25, 29/30/31, exact loads/stock/jobs and microstates. |
| `test_interpolation_before_after_reroute_and_backward_seek()` | Samples 4.5 and diversion midpoint; checks geometry/range and restores old active leg when seeking backwards. |
| `test_pause_resume_speed_and_horizon_stop()` | Controller calls; checks paused elapsed time ignored, speed 2, horizon clamp and end-step stability. |
| `test_refresh_cadence_does_not_choose_events_or_motion()` | Advances by one four-second interval versus eight half-seconds; asserts identical display state at 9. |
| `test_held_slot_waiting_load_and_each_plant_jobs()` | Reads 3a/4c fixtures; checks onboard blocked P2, held slot/queue and RF2 active work separate from RF1. |
| `test_partial_delivery_clears_local_unload_queue_with_other_load_onboard()` | Builds local display state and applies one admission; checks RF1 queue clears while RF2-bound P2 stays onboard. Only local dictionaries change. |
| `test_all_saved_formats_recycling_and_terminal_counts()` | Loads all saved fixtures and final-state files; compares terminal products, loads and plant stock across supported formats. Read only. |
| `test_horizon_mid_leg_uses_actual_travel()` | Writes temp mid-leg run via run_once; compares displayed final position/range with saved actual travel, keeps active leg. |
| `test_sparse_checkpoints_and_snapshot_isolation()` | Creates seeded zero-fleet run with >100 releases; checks sparse checkpoint and independent returned snapshots. Temp config/run only. |
| `test_replay_rejects_wrong_hash_order_and_unknown_records()` | Corrupts separate temp copies; checks early rejection of raw hash mismatch, sequence error and unknown transition. |
| `test_invalid_seek_speed_and_elapsed_inputs()` | Controller calls only; checks invalid inputs and unchanged previous display state. |
| `VisualizationTests.setUp()` | unittest → None; allocates scratch/cache paths, temporarily sets Agg environment and restores it on cleanup. |
| `VisualizationTests.call_cli()` | Tests; argv/expected exit → CompletedProcess; launches separate Python child, checks status, retains temp outputs. |
| `test_visualized_and_headless_runs_have_identical_evidence_and_pass()` | Runs real CLI in both modes, checks PNG header and five byte-identical core files; calls independent verifier on each, asserts PASS. |
| `test_existing_replay_render_is_read_only_and_event_exact()` | Renders copied saved run at #29 through actual replay CLI; compares every evidence hash before/after. |
| `test_buttons_seek_keyboard_and_timer_callbacks()` | Creates Agg viewer, calls actual widget/keyboard/timer callbacks, checks pause/speed/seek/step/focus/error/backend handling; closes resources. |
| `test_render_all_plants_and_jobs_in_details()` | Renders RF1/RF2 fixture at 4; checks both plant labels and P1 active job; writes temp PNG. |
| `test_optional_dependency_boundary_and_cli_guards()` | Injects missing dependency, checks setup guidance, uses fresh child to prove headless imports contain no Matplotlib, rejects viewer flags without --visualize and reports non-GUI backend. |
| `block_matplotlib()` (nested above) | Patched import hook; module name/args → normal import or injected ImportError. No files; only inside the dependency test. |

## Changed files and scope

- `simulation/__main__.py`: optional post-save viewer flags and lazy call.
- `simulation/replay.py`: read-only snapshots, sparse checkpoints, interpolation/controller.
- `simulation/visualization.py`: Matplotlib map/panels, controls, replay CLI and PNG rendering.
- `requirements-visualization.txt`: optional Matplotlib dependency.
- `tests/test_visualization.py`: 17 new tests and helpers.
- `docs/checkpoint_6_review.md`: this full walkthrough and function guide.
- `README.md`: installation and new-run/replay commands.

No physical simulation, experiment, hashing or independent-verifier rule changed.
No new metrics/CSV framework or later scope was added from the broader output
brief. Generated images/results, virtual environment and test logs remain ignored.
Stopped after checkpoint 6 for review.

## Viewer update: previous event and saved availability timetable

The viewer now has a **Previous event** button immediately before Next event and
Left/B shortcuts (with the same textbox focus guard). It pauses and decrements
the applied-event cursor by one. Stepping back from sequence 1 requests
`snapshot(0.0, 0)`, showing the state before all events. Another previous step
leaves that state unchanged. Next event then applies only sequence 1. This differs
from the existing Restart/Time 0 behavior, which settles all records at time zero.

There is now a third side panel, alongside the existing job information. Each
workstation has a facility-prefixed row, with facilities separated and colored
as groups. Bars use the actual `scenario.json` workstation `windows`; neither
generation settings nor active jobs replace those windows. The shared axis spans
the run horizon. A red marker follows the controller clock on every redraw,
including timer playback, seeks and both directions of event stepping. When two
adjacent records share a time, the marker correctly stays at that same time while
the record number, job state or remaining sequence changes. Actual jobs remain
in the original information panel. The figure is wider to keep all nine pilot
station labels readable without compressing them into the text panel.

Open the saved three-site run from the VS Code PowerShell terminal:

```powershell
& $thesisPython -m simulation.visualization .\results\three-sites-42 --time 10
```

Check all nine labels from RF1 / WS1 through RF3 / WS3, the blue/green/purple
availability groups, and the red marker at 10. Click Previous event: it pauses,
decreases the sequence by one, and moves the marker to that record's timestamp.
Same-time backward steps leave the marker at that time rather than skipping records.
Time and Event # seeks also reposition the marker; Resume moves it continuously.

For the earlier fixed-coordinate pilot (before coordinate generation), seed 42:

- Seek event **49**: CP1:P000001 is processing WS3, with remaining `[WS3 → WS2]`.
- Previous gives **48**, inventory return at the same time `5.521552782955404`.
- Previous gives **47**, WS1 completion at that same time, with the completed
  operation removed from the remaining sequence.
- Previous gives **46**, before completion: the product is working on WS1 again
  and its sequence is restored to `[WS1 → WS3 → WS2]`. The marker moves to the
  earlier recorded time. The underlying saved records are never edited.

If viewing another saved run, find an operation_complete sequence in its own
log and step backward across that record; sequence numbers above are pilot-specific.

Four focused tests were added to `tests/test_visualization.py`:

| Test | Check |
|---|---|
| `test_previous_event_reaches_initial_state_and_stops_there()` | Undo first time-zero release, stable initial state, then apply first record again. |
| `test_previous_event_uses_sequences_at_identical_times_and_pauses()` | Undo only P2 pickup while keeping its same-time arrival; pause running playback and decrement exact cursor. |
| `test_nine_saved_pilot_timetable_rows_windows_and_moving_marker()` | Save and reload generated pilot; compare all plotted bars to saved windows, assert nine visible/non-overlapping labels, check playback/backward/seek marker positions and unchanged evidence. |
| `test_previous_completion_restores_repeated_operation_before_same_time_start()` | Use route [1,3,1] with continuously open stations; undo same-time start/return/completion and restore the earlier WS1 plus the full repeated sequence. |

All **22 visualization/replay tests passed** with Matplotlib Agg. The saved
three-site pilot was rendered and inspected; all nine rows are readable. Actual
window/mouse interaction remains a local desktop check. No engine, generator,
verifier or result schema changes were made.

New/changed function contracts:

| Function | Caller, inputs/return and effects |
|---|---|
| `ReplayController.step_previous_event()` / simulation/replay.py | Previous callback/tests; no input → None. Pauses, reconstructs sequence n−1 or the pre-event initial snapshot. Changes only the controller display state; no source/cache/evidence mutation. |
| `draw_availability_timetable()` / simulation/visualization.py | Viewer constructor; Axes, saved scenario, starting time → Line2D marker. Draws one saved-window bar row per station with facility labels/groups. Changes only plot artists. |
| `MatplotlibViewer.step_previous_event()` / same | Button or Left/B; optional GUI event → None. Calls the backward controller method and redraws labels, jobs and marker. |
| `MatplotlibViewer.__init__()` / same, changed | open_viewer/tests; controller → initialized viewer. Adds side timetable, static saved-window artists and adjacent Previous button; retains widget/timer references. |
| `MatplotlibViewer.redraw()` / same, changed | Controls/timer; no input → None. Updates existing map/info and marker x data without rebuilding the scheduled bars. |
| `MatplotlibViewer.on_key()` / same, changed | Canvas key event → None. Routes Left/B to Previous in addition to existing shortcuts; typing focus still suppresses shortcuts. |

The new tests are called by unittest without arguments and return None. The
controller tests mutate only local display dictionaries. The two rendering tests
create cleaned temporary scenarios/runs/figures; they use existing run_once to
save evidence and RunReplay to read it, then close figures through cleanup.

## Viewer update: actual processing overlays

The timetable now places orange processing bands at 50% opacity above scheduled
availability, below the red clock marker. Bands are slightly narrower vertically
than availability rows, leaving the underlying colours visible at their edges
and through the overlay. A two-item legend distinguishes scheduled availability
and actual processing. This is presentation of recorded events; it neither changes
processing rules nor clips recorded processing to declared availability windows.
A closed-window overlap therefore remains visible for inspection.

`initial_view_state()` creates an empty in-memory `processing_history` for every
facility/workstation pair. On `operation_start`, the existing active job becomes
the live interval. On `operation_complete`, replay appends a copy of that job
with the **recorded completion time**, then clears the active job. The history is
part of the existing sparse, deep-copied display checkpoints, not the saved
simulation state or an additional output file.

Each redraw removes only prior processing artists, leaving scheduled bars intact.
It draws completed intervals at their recorded bounds and a current job from its
recorded start to the current viewing time. Its planned future end is not painted.
A zero-width interval at the start event does not paint future work. After completion,
the finished interval remains and the original availability colour resumes beyond
it. At a same-time next start, that new job first contributes only zero elapsed
width; its future processing appears as the replay clock advances.

All history comes from the current applied-event prefix. Previous/Next event or
seeking can therefore remove/reinstate a completion and restore/shorten its live
band without retaining future orange intervals. Records with equal timestamps
remain separate, while rows use `(plant_id, workstation_id)` rather than just the
workstation name. Seeking earlier also restores the product's operation sequence
through the existing next_operation replay logic.

Open a saved three-site pilot (use your saved folder name if different):

```powershell
& $thesisPython -m simulation.visualization .\results\coordinate-check\aware-42 --time 10
```

At time 10, inspect all nine rows: orange history for finished operations and a
partial band for any job still active. Resume to extend live processing; Pause
freezes it. To inspect a completion precisely:

```powershell
$run = '.\results\coordinate-check\aware-42'
$completion = Get-Content "$run\events.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object event_type -eq 'operation_complete' | Select-Object -First 1
& $thesisPython -m simulation.visualization $run --event $completion.sequence
```

Click Previous event to undo the completion. Its completed-history entry disappears
and the earlier active interval is drawn only through that earlier record's time.
Next event restores the completed interval. If records share a timestamp, band
endpoints can stay unchanged while active/history status changes; the applied
record number and job information distinguish those microstates. Seeking by time
still settles every record through that time.

All **27 visualization/replay tests passed** with Agg (22 existing + 5 new).
The saved coordinate-generated three-site pilot was rendered at time 10 and on
both sides of its first completion, with all nine rows readable and every source
artifact byte unchanged. Native window interaction remains a local desktop check.
No engine, generator, verifier, configuration or saved result schema changed.

Function contracts for this update:

| Function / location | Caller; input → return; effects |
|---|---|
| `initial_view_state()` / simulation/replay.py, changed | Replay construction; normalized scenario → new display dictionary. Adds empty per-facility/per-station history alongside active jobs. No file changes. |
| `apply_view_event()` / same, changed | Cache construction/snapshot reconstruction; display state/event → None. Completion copies the current job into its own row's history using event time and completion sequence, then clears active work. Only display state changes. |
| `draw_availability_timetable()` / simulation/visualization.py, changed | Viewer construction; Axes/scenario/time → marker Line2D. Still draws saved availability; adds the two-item legend and updated title. |
| `draw_processing_bands()` / same, new | Viewer redraw; Axes/scenario/current snapshot → ordered PolyCollection list, one per facility/station row. Draws completed history and elapsed active processing at alpha .5/z-order 3, independent of calendar windows. Changes artists only; snapshot is read only. |
| `MatplotlibViewer.__init__()` / same, changed | open_viewer/tests; controller → viewer. Retains references to static availability artists and initializes the replaceable processing-artist list. |
| `MatplotlibViewer.redraw()` / same, changed | Controls/timer; no input → None. Removes prior processing artists, draws the current prefix's bands and updates the clock marker; no data or evidence mutation. |
| `VisualizationTests.assert_processing_matches_cursor()` / tests/test_visualization.py, new | Tests; viewer → None/assertion. Independently pairs applied start/completion records by facility, station, product and operation index, comparing each plotted interval with completed time or the current clock. Read only. |

Five new test methods are called by unittest without arguments and return None:

| Test | Effects/check |
|---|---|
| `test_processing_history_is_cursor_scoped_and_undoes_completion()` | Pure replay snapshots: history appears only at completion, undo restores active work, and mutating returned history cannot alter cached state. |
| `test_processing_bands_grow_complete_and_follow_same_time_record_order()` | Agg viewer: start at zero elapsed width, grow with playback, retain history, distinguish same-time next start, remove future intervals on undo, and preserve original colour after final completion. |
| `test_processing_bands_keep_facility_rows_distinct_and_availability_visible()` | Agg viewer: RF2/WS1 never colours RF1/WS1; validates opacity/z-order/legend, preserved availability artists and no artist accumulation after seeks. |
| `test_recorded_processing_over_closed_window_is_not_clipped()` | Temporary copied evidence has a deliberately conflicting calendar for presentation testing; recorded work still crosses its closed gap. This is not claimed as a verifier-valid simulation. No repository fixture is edited. |
| `test_saved_three_site_processing_history_matches_event_prefix_and_is_read_only()` | Creates and reads a saved pilot, checks all nine rows against event prefixes including checkpoint 100, completion undo/redo and later/earlier seeks; renders a PNG and confirms all evidence bytes are unchanged. |

The existing nine-row timetable test now checks the retained availability artists
separately from actual-processing overlays. All generated runs/images stay in
cleaned temporary directories or ignored results folders. Changes remain limited
to replay presentation, its tests and documentation.
