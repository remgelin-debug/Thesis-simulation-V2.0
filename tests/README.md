# Regression inputs

Run from the repository root: `python -m unittest discover -s tests -v`.
Matplotlib tests skip if the optional visualization dependency is absent.

The matching verifier subprocess/report-exit assertions in the inventory,
dynamic, range, static-quality, dynamic-quality and recycling tests call
`tests.verifier_cli.verify_run_via_cli()` directly. Their scenarios and expected
physical/tamper results remain local. The test package also supports direct
invocation, for example `python -m unittest tests.test_inventory -v`.
Coordinate/network CLI helpers, text-report freshness checks and standalone-tool
checks keep their separate contracts; there is no common setup hierarchy.

## Format-one retirement tests

The current suite retires original scalar support; it does not delete the old
fixture/input bytes. `fixtures/checkpoint_1/` and
`inputs/checkpoint_1_speed_2.json` are **historical evidence**, used for rejection
checks rather than successful execution. To run them use
`simulation-v8-readable-code-layout` at
`1753c587da13442914743da841d867e712b6cdb1`.

The previous suite passed 288 tests. Changes are classified by purpose:

| Category | Tests and change | Preserved expectation |
| --- | --- | --- |
| A: retired support only | Remove `CheckpointOneTests.test_rejects_later_checkpoint_features` | That format's old artificial capacity/range restrictions are no longer active; retirement tests now reject the entire format. |
| A: scalar-only duplicate | Remove `NumericalPhysicsTests.test_legacy_numerical_boundaries` | Tiny-speed and nonadvancing-processing failures already execute under all four retained policies in their dedicated numerical tests. |
| B: move safeguards | Replace the other four `CheckpointOneTests` with the corresponding four `SingleProductTests` in `test_single_product.py` | Independently expected journey, full window fit, partial horizon state, raw hashes, repeatability and overwrite protection; assertions use inventory containers. |
| B: move audit acceptance | All twelve `IndependentVerifierTests` use a temporary explicit format-two example instead of archived scalar evidence | Workstation identity, completion deadlines/duration, missing/malformed files, exact sequences, forbidden operations, output reconciliation, standalone operation and raw-byte hashes. Expected inventory rule IDs and field layouts are asserted explicitly. |
| B: move helper inputs/subcases | KPI partial/speed checks; two repeated-operation viewer tests; long-history replay setup; normal/optimized run comparison; discrete/default fixture checks; malformed-provenance subcase | Same expected quantities, times, labels, prefix/history behavior and failure classes. Tiny valid geometry now also executes all four retained policies. |
| C: retained assertions | All other inventory/network tests and their original physical assertions | Static/dynamic choices, F1–F8, optional graphics and standalone tools stay covered. Schema/unaware/all-saved-format loops remove only the retired support subcase; separate rejection tests cover its historical evidence. |

`tests.single_product.single_product_scenario()` constructs explicit format-two
inputs; it is a test-only data factory, not a runtime format-one converter.
The four new journey methods retain the hand-calculated operation times
4–6, 8–11 and 12–13, distance 4 and inventory wait 3; speed 2 gives wait 5.

`test_format_retirement.py` adds eleven tests: simulator retirement/no folder,
missing/unsupported declarations, fresh INCOMPLETE over every previous report
status, preferred/root/standalone verifier exits, hash-violation precedence,
replay/viewer rejection, mixed KPI discovery, refresh exclusions/attempt retention,
single-product formats 2/3 under every permitted policy, explicit tool checks for
missing/malformed declarations, and continued rejection
of static network overrides. Fractional-sequence coverage is renamed from
`test_fractional_sequences_fail_in_all_three_replay_formats` to
`test_fractional_sequences_fail_in_all_retained_policy_paths` and covers all four
retained paths. No remaining test is weakened to accept changed retained behavior.

### Retirement acceptance against readable-layout

The untouched `1753c587` baseline passes **288 tests**. The retirement suite has
**297 methods**: two obsolete methods removed, four journey methods migrated to
the new class, one fractional-sequence method renamed, and eleven new rejection/
retained-scope methods. All 281 other exact test identities remain. The migrated
verifier acceptance class still has its twelve methods and concrete assertions.
The final ordinary suite passes all 297; 56 focused tests also pass under `-O`,
including always-active guards, numerical boundaries, delivery service, unaware
forecast guards, single-product execution and unsupported-format handling.

| Controlled comparison | Result |
| --- | --- |
| Retained acceptance matrix | 30/30 baseline and updated runs verify; 150/150 physical artifacts and 60/60 JSON/text reports are byte-identical. |
| Matrix composition | Keep all 25 applicable format-two/three cases, replace two scalar cases with explicit single-product format-two cases, add seed-43 unaware and seed-54 aware/unaware. This is not the old three-format matrix unchanged. |
| Additional paired seeds/experiments | 12 seed-42/43/54 runs and two eight-run experiments match another 140 physical artifacts and 56 reports. Reversed execution order/repeat inputs and outputs agree. |
| Additional CLI runs | Eight runs match 40 physical artifacts, 16 reports and KPI CSV bytes; explicit integrated/saved PNGs match. |
| Run manifests | All 66 compared manifests match raw bytes; run IDs are checked against their own folders. Four full experiment manifests differ only at explicitly checked source/output directory prefixes, including commands/log paths. |
| Retained invalid evidence | 69 cases keep all 138 JSON/text report bytes, finding order/context/expected-observed values and CLI exits: 16 FAIL, 53 INCOMPLETE. Repeated refresh and explicit I/O failures remain covered. |
| KPI export | Paired seed batch: 12 rows, zero skips, baseline-identical CSV. Additional CLI batch: eight rows, zero skips. Missing-evidence refresh: seven rows/one named skip; malformed dispatch refresh: eight rows/zero skips with one fresh INCOMPLETE label. |
| Replay/presentation | 93 complete snapshot comparisons and six Agg RGBA images match; nine workstation rows, processing overlays and callbacks remain tested. |

Historical format-one reports intentionally become unsupported; unknown/missing
format declarations get explicit scope diagnostics. Those are not retained-run
report equivalence claims. Only two historical diagnostic placeholder strings and
known-container shape checks remain for retained malformed-evidence compatibility;
none executes a scalar journey. Root/standalone tools, plotting-blocked simulation,
optional `--visualize`, previous/next controls and no-overwrite behavior pass.
Graphics checks use Agg, not an interactive Windows desktop. No performance claim
is made. Configurations, archived inputs/fixtures, docs/history and both original
uncommitted review reports remain unchanged; generated acceptance evidence stays
outside tracked source.

`fixtures/checkpoint_*/` contains immutable audited runs (scenario, events,
dispatch records, manifest, summary, state and verification). Their scenario
hashes are byte identities: `.gitattributes` pins these JSON files to LF.
Do not reserialize them to update paths.

Tests that formerly loaded checkpoint configurations now load the identical
scenario in an existing retained fixture for checkpoints 3a, 3c (range 6), 4a, 4b and
4c. This removes redundant JSON copies without changing test inputs/coverage.

`inputs/` holds eight unique historical test configurations: speed-2; recycling
on/off; range-5, partial-leg, empty-recycler and unlimited cases; and the seeded
four-policy experiment condition. These are regression inputs, separate from
current user-facing `configs/three_site_pilot.json`. Tests construct modified
scenarios in temporary directories; generated output never belongs in Git.

`test_refactor.py` checks package/CLI boundaries, running without plotting,
standalone tools, policy reuse of resolved inputs and repeatability. Existing
physics, routing, generation, tamper-detection, replay and KPI tests remain.

The stage tests additionally cover typed records, snapshot ownership, unaware
forecast guards and consolidated execution ordering. KPI tests integrate saved
events through the real exporter and check physical line numbers across blank
lines. Their generated/corrupted evidence stays in temporary directories.

Historically, the stage-5 suite had 195 tests when the optional viewer dependency is
installed. It covers report/exit-code tampering, missing-evidence experiment
refresh, paired-policy order, isolated verifier/KPI copies and viewer callbacks
under Agg. An Agg check exercises rendering and controls without opening a
desktop window.

The 27-case cross-revision acceptance matrix compares scenario, events, dispatch,
summary and final-state bytes plus manifests. Tool comparisons also exercise
valid, missing, malformed, tampered and unsupported evidence. Historical results
are recorded in `docs/history/refactor_acceptance.md`. They are separate from
audited fixture expectations. The stage-4 seed-54 routing failure is documented
in the architecture guide as a historical case fixed by the delivery-service branch.

`test_delivery_service.py` adds ten focused F1 tests. They cover repeated visits,
already-onboard later deliveries, partial blocked admissions and held slots,
same-time capacity release, horizon waits, mixed recycler loads, independent
plant/recycler forecasts and isolation, seed 54 under both policies, unauthorized
acceptance tampering, and replay/KPI completion of a wait while future load stays
onboard. The delivery-service suite has 205 tests with optional Matplotlib installed.
The generated seed-42 unaware KPI expectation changes from one unfinished part to
two because CP1:P000002 now waits for its authorized later RF1 visit. Its precise
physical state is also asserted. Historical fixture bytes are unchanged.

`test_verifier_discrete.py` adds eleven F2 tests for coherent stream-seed tampering,
fractional sequences in all replay formats, nested integer fields and score
positions, bool/float integer impostors, full large-seed precision and preserved
physical tolerance. They run the real verifier on owned copies and leave audited
scenario bytes unchanged. ID-keyed physical-time maps have a separate collision
regression so field names cannot accidentally redefine product or vehicle IDs.

`test_verifier_structure.py` adds twelve F3 tests. Provenance shape/refresh cases
cover scenario, manifest or both; list/string/null; absent or existing
PASS/FAIL/INCOMPLETE reports; and repeat verification. Other cases cover stream
maps and missing derived keys, malformed candidates/proposals/events/state,
combined violations, raw-byte hash checks with unusable declarations, explicit
report I/O failures and experiment-refresh/KPI labels. All evidence copies are
temporary. The complete verifier-evidence suite contains 228 tests with optional
Matplotlib installed. Valid reports and simulation artifacts are compared against
an untouched delivery-fix checkout; historical fixtures are not regenerated.

`test_physical_guards.py` corrupts owned API state to test mandatory guards in
normal, `-O` and `PYTHONOPTIMIZE=1` subprocesses. It also compares valid retained
fixed and dynamic artifacts across interpreter modes.
`test_numerical_physics.py` covers representable physical time and geometry,
over/underflow, horizon/window controls, copied forecasts and independent
rejection of previously false-PASS zero-time operations. Run these directly with:

```powershell
& $thesisPython -m unittest discover -s tests -p 'test_physical_guards.py' -v
& $thesisPython -O -m unittest discover -s tests -p 'test_numerical_physics.py' -v
```

These temporary invalid-state/evidence probes do not change audited fixtures.

`test_performance_calendars.py` checks prepared/direct fitting equivalence, busy
and no-work starts, same local workstation IDs at different plants, immutable
calendar reuse and invalidation, candidate isolation and F5 arithmetic rejection.

`test_performance_candidates.py` compares the streamed reset-variant traversal
with the original eager breadth-first reference, including complete candidate
JSON, duplicate retention, rejected alternatives and nested-route isolation.

`test_performance_replay.py` compares full snapshot JSON with replay from the
initial state, including randomized exact prefixes, distant/time seeks, same-time
completion/start controls, long histories and mutation isolation. It checks that
checkpoints do not copy completed history and compares nine-row Agg pixels with
the full-history reference. The complete performance-memory suite has 272 tests.

Historical wrapper removal retained all 272 test methods. At that revision,
checkpoint-one tests imported the physical engine directly. They have since been
replaced by inventory single-product tests below. Saved-run rendering and no-desktop error checks invoke
`tools.viewer`; all image, read-only evidence, event-cursor and backend assertions
remain. The former viewer-alias help check now covers the supported simulation
CLI and its `--visualize` flag. Root verifier, experiment and KPI wrapper coverage
is retained.

The confirmed simplification cleanup retains those 272 test identities and adds
16 focused tests, for 288 passing methods in ordinary and optimized execution.
`test_simplification.py` pins canonical serialization, the verifier's narrower
copy/diagnostic ownership, physical conservation error ordering and the shared
CLI assertion contract. Calendar tests add independent explicit interval/type/
ownership expectations, so cached/direct comparison is not the only oracle after
the merger is shared. Audited inputs/fixtures and original review reports stay
unchanged. S08 viewer caching and D1 failure-artifact work remain outside scope.
