# Historical refactor acceptance records

These are the recorded stage-by-stage comparisons and former responsibility
layouts. Current commands and interfaces are documented in README.md and
docs/architecture.md. Paths to external acceptance evidence describe the original
execution environment.

## Responsibilities recorded before stage 4

Stage-3 execution was split as follows, recorded before the stage-4 implementation.

| Path | Queue and same-time order | Settlement and departure boundary |
| --- | --- | --- |
| Format one | (time, priority, action), with completion 0, release 1, arrival 2, start 3, dispatch 4 | One event at a time. Arrival/completion schedules the next processing start. |
| Format-two static | (time, priority, entity ID, action), with completion/window 0, release 1, arrival 2, dispatch 3 | Drain the current batch, settle admissions/work, then execute scheduled departures. Newly created same-time events form the next batch. |
| Format-two dynamic | Runner converts singular sites to the format-three execution shape | Uses the dynamic sequence below, retaining single-plant saved fields. |
| Format-three dynamic | (time, priority, identifier, action, token), with completion/window 0, release 1, arrival/initial availability 2 | Settle plants, finish served stops, start committed legs and drain new same-time events before routing. Releases/availability trigger routing. |
| Dynamic candidate projection | Reconstruct active arrivals/completions and current settlement from copied state | Same dynamic execution with dispatch disabled, collecting revised delivery ETAs. |

Dynamic stale arrivals are discarded at the heap head before advancing motion or
the clock. A stale arrival within an otherwise valid batch is skipped during
processing. Multi-plant completions use plant/station identity. Windows themselves
record no event. The heap's existing identifier/action/token ties determine batch
order. Legacy scalar state and single-plant output layouts are compatibility
requirements. Separate copies of inventory release, completion, clock, settlement,
conservation and horizon handling are consolidation opportunities.

Stage 2 split selection across routing and forecasting. Fixed dispatch in routing
built observations, checked range, selected products, logged the decision and
applied assignments. Forecasting also selected static aware products and computed
both aware policies' scores. Dynamic routing enumerated and selected insertions,
and the dynamic engine applied them. Both unaware paths already bypassed the
processing forecast pipeline. Stage 3 gives selection to routing and fixed
assignment application to the fixed engine while retaining those call times.


| Responsibility | Stage 3 | Stage 4 |
| --- | --- | --- |
| Inventory clock, releases and completions | Separate fixed and dynamic loops | One `execute_inventory()` loop |
| Plant settling and conservation | Separate loop invocation paths | Shared network settlement and checks for every inventory run |
| Horizon travel and waits | Two implementations | `finish_inventory_horizon()`, counting each plant once |
| Fixed stop progression and departures | Fixed engine | Explicit fixed helpers in engine |
| Dynamic triggers and stale-leg handling | Dynamic engine | Explicit dynamic helpers in engine |
| Legacy scalar execution | Fixed engine | Specialized `engine.run_simulation()` |
| Former Python import paths | Execution code in fixed_engine | Compatibility exports from engine |


## V7 acceptance evidence

Before editing execution modules, 175 tests passed and 26 independently verified
baseline runs were captured outside the repository. Cases cover both dynamic
policies, both fixed policies, formats 1–3, pilot seeds 42/43, reroutes and partial
legs, rejected/finite/unlimited range, empty recycler resets, seeded recycling,
finite/held-slot inventory and an empty fleet. Refactored scenario, event,
dispatch, summary and state files match all 130 baseline files byte for byte.
The unchanged physics tests retain hash/tamper detection and replay coverage.

The focused refactor tests additionally check preferred/compatibility commands,
standalone tools, seeded reuse, and a full CLI run with plotting imports blocked.
Rendering is exercised with Matplotlib's Agg backend; interactive window controls
still need a desktop to inspect visually. Generated acceptance artifacts live
under ignored `results/`; external original baselines are not required by tests.

Final checks: **180/180 tests pass** (175 retained + 5 refactor acceptance checks).
The preferred experiment command produced eight PASS runs in two matched
replications; all four policy hashes match within each replication. KPI export
produced 26 baseline rows and eight experiment rows with zero skipped runs.
A saved pilot rendered under Agg with all nine workstation rows and processing
overlays intact. A full CLI run succeeded with Matplotlib, NumPy and Tk imports
blocked; replay and standalone verifier/exporter require no plotting libraries.
Production Python files decreased from 27 to 24 (including compatibility scripts);
simulation implementation modules decreased from 24 to 15 including its package
initializer and viewer compatibility command. The pilot configuration and audited
fixture scenario bytes are unchanged.

## V8 stage-1 acceptance evidence

Starting V7 commit: `0c95f23ca4089134b634d1a1372c133832b54c44`, with a clean
working tree and no later commits after fetching. Before edits, all 180 tests
passed. The 26 established acceptance cases were recaptured from V7 outside Git,
plus an explicit active-processing-at-horizon case. All 27 baseline runs passed
independent verification. Comparisons require exact bytes for scenario, events,
dispatch, summary and final state, and compare every manifest field except
`run_id`. Each run ID is checked against its own output directory name, and each
scenario hash is recomputed from saved bytes. Neither fixtures nor expected
physical results are regenerated to accommodate this refactor.

Stage-1 results: **181/181 tests passed** (all 180 existing tests plus one focused
record-schema check against real audited fixed/dynamic/forecast dictionaries).
All **135 artifact files** matched byte-for-byte across 27 cases; all 27 manifests
matched after the explicit directory-name run-ID check and all 27 verifier
results were PASS. The eight-run experiment command passed with identical input
hashes across policies within each replication. KPI export produced 27 rows with
zero skipped runs. Seed repeatability, both-policy resolved replay, policy-order
independence, standalone checker/KPI copies, compatibility CLIs and replay controls
passed the retained tests. The seeded pilot CLI also passed with Matplotlib,
NumPy and Tk imports blocked and matched all five normal-run artifacts. The saved
pilot rendered with Agg; interactive desktop rendering remains a local check.
Function-body comparison confirmed all 80 retained simulation functions preserve
execution statements after excluding imports/docs/types and normalizing the one
removed motion delegate. Tools other than the experiments import are unchanged.

## V8 stage-2 acceptance evidence

All 181 stage-1 tests passed before editing. The 184 stage-2 tests include three
new checks for lookup ownership, validation without copying, and forecast copying
that excludes unreleased products and event and decision logs.

Across 27 runs covering all four policies and all three formats, the scenario,
event log, dispatch log, summary and final state matched stage 1 byte-for-byte.
Each manifest matched after checking its run ID against its own folder name.
All 27 runs passed independent verification. The pilot checks covered seeds 42
and 43, repeating seed 42 and replaying its saved inputs under both dynamic
policies. A two-replication experiment produced eight verified runs. KPI export
produced 27 rows, and the saved pilot replay rendered with the Agg backend.

## V8 stage-3 acceptance evidence

The fetched stage-2 tip was `64ed2746904876fc907d230deef72db8f5b71e22`,
with a clean working tree and no additional commits. A separate checkout ran all
184 starting tests and recaptured the established 27-run matrix before edits.

All 188 stage-3 tests passed. The four additions guard unaware execution with
raising forecast entry points, confirm real aware forecast calls, check that
static selection leaves assignments to the engine, and evaluate dynamic
candidates in both orders to check independent projections. The existing tests
retain commitment, range, capacity, held-slot, future-return and horizon cases.

All 135 scenario/event/dispatch/summary/final-state artifact comparisons matched
stage 2 byte-for-byte. All 27 manifests matched after validating each run ID
against its own output folder. Every baseline and refactored run passed independent
verification. The matrix includes all four policies, formats 1–3 and pilot seeds
42 and 43. Seed-42 repetition and exact resolved-scenario reuse across dynamic
policies passed.

The experiment command produced eight verified runs across two replications,
with matching scenario hashes within each replication. Repeat/reversed-policy
experiments passed the suite. KPI export produced 27 rows and skipped zero runs.
Standalone verifier/KPI copies, compatibility commands, replay controls, all nine
workstation rows and processing overlays passed their retained tests. A seeded
pilot CLI run with optional plotting imports blocked matched all five baseline
artifacts. The viewer rendered under Agg and its callbacks were exercised under
that backend. Interactive Windows desktop operation remains a local check.

## V8 stage-4 acceptance evidence

The fetched stage-3 tip was `89cf94545a9060480ccbc69921106e83637fccf5`.
There were no additional commits or local edits. A separate checkout passed all
188 starting tests and captured the established 27-run matrix before implementation.

All 194 final tests passed. Six new tests cover simultaneous fixed completion,
release, arrival and departure; same-time held-slot return and unload retry;
zero-distance batch ordering; arrival/start/completion at the horizon with finite
and unlimited range; stale-arrival rejection before physical time advancement;
and single/network container ownership across private snapshots.

All 135 scenario/event/dispatch/summary/final-state artifact files matched the
stage-3 baseline byte-for-byte. All 27 manifests matched after checking each
directory-dependent run ID against its own output folder. Every valid baseline
and final run passed independent verification. The matrix includes all policies,
all supported formats, seeds 42/43, repeated seed 42, reroutes, finite/held-slot
inventory, blocked unloading, recycling, range rejection and horizon truncation.

The experiment command produced eight verified runs in two matched replications.
KPI export produced 27 rows with zero skips. The suite retained checks for reversed
policy order, standalone tools, compatibility commands, replay callbacks and
all nine workstation timetable rows with processing overlays. A seed-42 pilot
with plotting imports blocked and its paired unaware resolved replay both passed
verification and matched their respective baseline artifacts. Rendering used Agg;
interactive Windows desktop operation remains a local check.

## V8 stage-5 acceptance evidence

The fetched stage-4 tip was `a3154aa5bcbd89f5d4ba958f8170bf3f6f7c1079`,
with no newer commits or local edits. A separate checkout passed all 194 starting
tests and recaptured the established 27-run matrix. Stage 5 adds one streamed-KPI
diagnostic regression. All 195 final tests passed.

All 135 simulation artifact comparisons matched stage 4 byte-for-byte, including
seeds 42/43 and repeated seed 42. All 27 manifests matched after validating each
directory-dependent run ID. Every valid baseline and final run passed independent
verification. The simulation package, pilot configuration and historical fixture
bytes were unchanged.

Verifier/replay comparisons covered 42 folders with valid, missing, malformed,
tampered and unsupported evidence. Report JSON/text, classifications, exit codes,
stdout/stderr and replay snapshots or errors matched exactly on the same evidence
paths. The mixed-batch KPI CSV, including BOM, column order, values, skipped-run
diagnostics and exit code, also matched byte-for-byte. Source evidence hashes
were checked before and after tool execution.

Two-replication experiment commands produced eight verified runs on each version.
All 40 corresponding simulation artifacts and all four experiment CSVs matched
byte-for-byte. Manifests matched after replacing only the explicit checkout and
experiment-root path prefixes. Removing one summary in separate refresh copies
produced the same INCOMPLETE finding, missing policy and excluded comparison group.
Four rows in the unaffected replication remained eligible. Extracted statistics
reproduced the previous valid and incomplete exports exactly.

The documented seeds-1–20 pilot workflow completed 40 runs and 40 PASS
verifications with byte-identical paired scenarios, then exported 40 KPI rows
with zero skips. An earlier seeds-42–61 attempt stopped at the inherited seed-54
unaware routing failure after 25 completed verified runs. Stage 4 reproduces the
same failure on the same scenario. Its diagnosis is in the current architecture
guide, and the core behaviour was preserved.

Retained tests covered both unaware forecast guards, seed/policy-order
repeatability, standalone tool copies, compatibility commands, experiment refresh,
event-prefix replay, all nine workstation rows and processing overlays. A
plotting-blocked seed-42 CLI run and verification matched the baseline's five
artifacts. Baseline and final Agg viewer PNG bytes matched. Interactive desktop
operation was not exercised.
