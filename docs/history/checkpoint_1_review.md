# Checkpoint 1 function review

All functions below are new; the initial repository had no code. Module paths
are relative to the repository root. Functions returning `None` perform the
listed mutation rather than returning a new state. `Record` is the shared
`dict[str, Any]` type alias used for JSON-shaped records. `SimulationState` is a
dataclass; its generated constructor initializes the declared fields and each
list/dict factory produces a new container per run. It writes no files.

## Call path with the actual fixture

1. Python executes `simulation/__main__.py`; `main()` parses the resolved fixture
   path, optional fixed policy and output path. It calls `run_once()`.
2. `run_once()` calls `load_scenario()`, which calls `validate_scenario()`. The
   fixture passes; its original bytes are retained for hashing and saving.
   The runner creates a new output directory and saves the scenario and running
   manifest, then calls `run_simulation()`.
3. The engine constructs `SimulationState(vehicle_location="RF1")`, then pops
   events from a heap. At 0 it releases P1 at CP1 and logs release. Departure at
   0 calls `dispatch_fixed_trip()`, recording the policy input before logging
   assignment. The dispatcher assigns P1 to AV1 with planned destination RF1.
4. `depart_leg(RF1, CP1)` computes `sqrt((2-0)^2 + (0-0)^2) = 2`, so at speed
   1 its return value is arrival time 2. The vehicle becomes travelling;
   P1 remains physically at CP1. At 2, `arrive_leg()` adds 2 distance units.
   `pick_up_product()` removes P1's collection ownership by changing its state
   and location and adding it to AV1's load. `depart_leg(CP1, RF1)` returns 4.
5. At 4, `arrive_leg()` makes cumulative travel 4 and places AV1 at RF1.
   `admit_product()` empties its payload, adds P1 to shared inventory, and
   records actual destination RF1. `next_operation_start()` finds WS1 and
   returns `(4, WS1_record, [4,6])`. The engine schedules a start at 4.
6. `start_operation()` removes P1 from inventory, adds `4-4=0` waiting time,
   sets the active operation at index 0, and returns 6. The engine schedules
   completion at 6. This operation finishes exactly at closing time: permitted.
7. At 6, `complete_operation()` records history and changes index 0 to 1.
   P1 returns to shared inventory with entry time 6. The next-operation search
   finds operation 3: `max(6,8)=8`, and `8+3=11 <= 11`; the engine schedules 8.
8. At 8, start removes P1 from inventory and adds `8-6=2` waiting units. It
   schedules completion at 11. At 11, completion advances index 1 to 2 and
   returns P1 to inventory. WS2's next fit is 12, ending at 13.
9. At 12, start adds `12-11=1` waiting unit, making 3 total. At 13 completion
   changes index 2 to 3, sets state `remanufactured`, clears physical location,
   and records completion 13. It does not return a finished product to inventory.
10. At 14 the engine logs the horizon and returns its state. The runner writes
    event and decision JSONL, final state, summary, and completed manifest.
    `main()` prints the summary and returns 0; `SystemExit` gives the shell 0.

## Function contracts

| File / function | Caller and timing | Receives → returns | Changes and guards |
| --- | --- | --- | --- |
| `simulation/__main__.py`: `main()` | Python module entry, once | No parameters (reads CLI) → integer 0 on success | Calls runner; prints summary. Argparse rejects other policies/unknown arguments. Read/validation/write errors exit 1 with a message; argparse syntax errors exit 2. |
| `simulation/runner.py`: `run_once()` | `main()` after parsing | Scenario `Path`, new output `Path` → summary record | Calls loader, engine, serializer. Creates directory and six artifact files. Refuses an existing directory. Hashes saved input bytes. Marks completed last. |
| `simulation/config.py`: `load_scenario()` | Runner; tests | Input `Path` → `(scenario_record, original_bytes)` | Reads file, parses JSON, calls validator. Does not mutate scenario or write files. |
| `simulation/config.py`: `validate_scenario()` | Loader; scope test | Scenario record → `None` | No mutation. Checks format/policy, unlimited limits, remanufacturing, IDs/references, fixed trip, payload, finite numbers, positive speed/durations, compatible operations, ordered separated windows and release ≤ departure ≤ horizon. Raises on invalid inputs. |
| `simulation/engine.py`: `run_simulation()` | Runner; journey and horizon tests | Validated scenario → new `SimulationState` | Owns heap, clock and release transition; invokes physical transition functions and records horizon. Schedules calculated arrivals, starts and completions. Processes timestamps ≤ horizon. Accounts for partial travel and remaining inventory wait at horizon. No files. |
| `simulation/models.py`: `record_event()` | Engine, routing, transport, plant after each transition | State, event-type string, keyword details → `None` | Appends a record containing `sequence=len(events)+1`, current time, type and details. Does not advance the clock. |
| `simulation/routing.py`: `dispatch_fixed_trip()` | Engine on fixed departure | Scenario, state → `None` | Appends one decision with prior event sequence and actual quality-unaware input, then sets assignment/planned destination and logs assignment. Single trip/product means no scored alternatives or ties; candidates is empty with an explanation. Physical location stays at collection. |
| `simulation/transport.py`: `depart_leg()` | Engine after dispatch and pickup | Scenario, state, origin and target site records → arrival time | Uses Euclidean distance/speed; stores full active leg including coordinates, times and copied load. Sets travelling status and departure-site location; logs departure. Distance is accumulated at arrival or partial horizon, never here. |
| `simulation/transport.py`: `arrive_leg()` | Engine at scheduled collection/plant arrival | Scenario, state → `None` | Requires an active leg at its exact arrival time. Adds distance, sets target location and idle status, clears leg, logs arrival. Scenario parameter keeps transport transition signatures consistent. |
| `simulation/transport.py`: `pick_up_product()` | Engine after collection arrival | Scenario, state → `None` | Guards physical collection state, location, assignment ownership and empty payload; changes product to onboard, sets AV location, appends load and pickup timestamp, logs pickup. |
| `simulation/transport.py`: `admit_product()` | Engine after plant arrival | Scenario, state → `None` | Guards plant location and onboard membership; removes load, appends shared inventory, sets entry/admission times and actual destination, logs admission. Unlimited inventory only. |
| `simulation/plant.py`: `next_operation_start()` | Engine after admission/intermediate completion; window test | Scenario, state → `(start_time, station_record, window)` or `None` | No mutation. Finds station by next operation ID. Tests each window using `s=max(now,a)`, `s<b` and `s+d<=b`. Returns first fit; no fit leaves the product waiting. |
| `simulation/plant.py`: `start_operation()` | Engine at calculated start | Scenario, state, station record, window → end time | Requires idle active-operation state and inventory entry. Removes inventory membership, accumulates wait, clears entry time, creates active operation, sets workstation location, logs start. Engine owns scheduling. |
| `simulation/plant.py`: `complete_operation()` | Engine at calculated completion | Scenario, state → `None` | Guards active operation and exact end time. Copies history, clears active machine, increments index once, logs completion; either returns to inventory with new entry time/log or records terminal state/completion. |
| `simulation/runner.py`: `write_json()` | Runner for manifest, final state, summary | Destination `Path`, JSON-serializable value → `None` | Writes/replaces UTF-8 indented JSON plus newline; rejects nonfinite JSON numbers. No simulation-state mutation. |

There are no other handwritten runtime functions or methods. `__init__.py`
contains only a module docstring. Standard-library constructors, `heapq`,
`json`, `Path`, and `dataclasses.asdict` are library operations, not additional
project functions. There is no existing project call path to explain.

## Tricky boundary calls

- `next_operation_start(scenario, SimulationState(time=4))` with index 0 returns
  start 4 and `[4,6]` for duration 2. With time 4.5 it returns `None` because
  `4.5+2 > 6`; a window being open is insufficient by itself.
- At time 6 and next index 1, the same function returns start 8, WS3, `[8,11]`.
  At time 11 and index 2 it returns start 12, WS2, `[12,13]`.
- `complete_operation()` at time 6 transforms `processing/WS1`, empty inventory,
  next index 0 into `in_plant_inventory/RF1`, inventory `[P1]`, next index 1.
  At 13 it transforms `processing/WS2`, index 2 into `remanufactured`, physical
  location `None`, index 3; inventory remains empty.
- `run_simulation()` with the same scenario except horizon 1 retains outbound
  leg `RF1→CP1`, arrival 2, and product at CP1. Distance is
  `(1-0)/(2-0)*2 = 1`. Its interpolated vehicle position is `(1,0)`.
  At horizon 7 it retains P1 in inventory, waiting since 6, with wait 1.
  At horizon 9 it retains WS3 processing with scheduled completion 11.
- `record_event(state, "pickup", product_id="P1", vehicle_id="AV1",
  collection_point="CP1")` at time 2 appends the next sequence and time 2.
  It records a transition already applied; it does not perform pickup itself.
- `write_json(output_directory / "summary.json", summary)` serializes the
  observed `1/1/0/0` counts and distance 4 / inventory wait 3 to disk.

## Every test method

The standard-library `unittest` discovery runner constructs
`CheckpointOneTests` and calls each method with only its `self` instance.
Each returns `None` (assertions raise on failure). None changes the fixture.

| Method in `tests/test_checkpoint_1.py` | Calls and receives internally | Changes / checks |
| --- | --- | --- |
| `test_expected_journey()` | Loads fixture, calls engine, receives state | Allocates in-memory run; compares full milestone sequence, operation order and starts/ends, distance 4, wait 3, empty containers, idle vehicle, unique sequences and release before departure. Checks policy input lacks quality/operations. No files written. |
| `test_window_requires_full_fit()` | Loads fixture, constructs state at 4; calls calendar search at 4 then 4.5 | Changes only local clock; exact finish at 6 accepted, late finish rejected. No files written. |
| `test_horizon_preserves_unfinished_work()` | Loads a fresh fixture per subtest; calls engine with horizons 1, 7, 9, 13 | Changes local horizon only. Checks partial travel, inventory wait, active processing, inclusive completion at 13, and no events beyond horizon. No files written. |
| `test_cli_writes_reproducible_artifacts()` | Runs `sys.executable -m simulation` twice in subprocesses | Creates two temporary output directories and all artifacts, checks success, copied bytes, SHA-256, completion status, milestones, summary and decision sequence. Compares artifact bytes across runs except run-ID-bearing manifest. Tries existing directory and expects failure. Temporary directory cleanup removes all test files. |
| `test_rejects_later_checkpoint_features()` | Loads fixture per subtest and calls validator | Changes local inventory capacity or range to 1, expects `ValueError`. No files written. |

The test expectations come directly from the hand-calculated Section 12 fixture,
not from querying the implementation for its own expected results. These are
unit/integration checks; the independent saved-run verifier belongs to checkpoint 2.
