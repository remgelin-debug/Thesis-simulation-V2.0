"""Check independent verification of valid, altered and missing evidence.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from simulation.runner import run_once
from tests.single_product import single_product_scenario

ROOT = Path(__file__).resolve().parents[1]


class IndependentVerifierTests(unittest.TestCase):
    """Keep every mutation in a fresh temporary single-product inventory run."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate a private run copy and register cleanup after each test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_directory = Path(self.temporary.name) / "run"
        self.scenario = single_product_scenario()
        self.source = Path(self.temporary.name) / 'scenario.json'
        self.source.write_text(json.dumps(self.scenario) + '\n', encoding='utf-8')
        run_once(self.source, self.run_directory)

    def run_verifier(self, expected_exit: int) -> dict[str, object]:
        """Execute the standalone CLI, assert its exit code and read both reports."""
        result = subprocess.run([sys.executable, str(ROOT / "verify_simulation.py"),
                                 str(self.run_directory)], capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        report = json.loads((self.run_directory / "verification.json").read_text())
        self.assertEqual(report["exit_code"], expected_exit)
        self.assertIn(report["status"], (self.run_directory / "verification.txt").read_text())
        return report

    def change_event(self, sequence: int, field: str, value: object) -> None:
        """Change one field in a copied event; leave source fixture and other evidence intact."""
        path = self.run_directory / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[sequence - 1][field] = value
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    # Check the expected model or evidence behavior

    def test_original_passes_without_modifying_evidence(self) -> None:
        """Verify the inventory example, recalculate wait three and preserve all input bytes."""
        before = {path.name: path.read_bytes() for path in self.run_directory.iterdir()}
        report = self.run_verifier(0)
        self.assertEqual(report["recalculated_summary"]["plant_inventory_wait"], 3)
        self.assertEqual(report["findings"], [])
        for name, contents in before.items():
            self.assertEqual((self.run_directory / name).read_bytes(), contents)

    def test_speed_two_passes_with_wait_five(self) -> None:
        """Generate the second scenario through the CLI, then independently verify its five-unit wait."""
        self.run_directory = Path(self.temporary.name) / "speed_two"
        self.scenario['vehicles'][0]['speed'] = 2
        self.source.write_text(json.dumps(self.scenario) + '\n', encoding='utf-8')
        result = subprocess.run([sys.executable, "-m", "simulation", "--resolved-scenario",
            str(self.source), "--output", str(self.run_directory)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.run_verifier(0)
        self.assertEqual(report["recalculated_summary"], {"generated": 1, "remanufactured": 1,
            "recycled": 0, "unfinished": 0, "vehicle_distance": 4, "plant_inventory_wait": 5, "waiting_to_unload": 0.0})

    def test_wrong_workstation_fails_with_event_details(self) -> None:
        """Change WS1 to WS9 at sequence nine and check the exact identity diagnostic."""
        self.change_event(9, "workstation_id", "WS9")
        report = self.run_verifier(1)
        finding = next(item for item in report["findings"] if item["rule"] == "workstation.known_identity")
        self.assertEqual((finding["event_sequence"], finding["time"], finding["expected"], finding["observed"]),
                         (9, 4, True, False))
        self.assertEqual(finding["ids"], {"product_id": "P1", "plant_id": "RF1", "workstation_id": "WS9"})

    def test_wrong_completion_time_fails(self) -> None:
        """Change WS1 completion time/end to seven and isolate both time and duration violations."""
        self.change_event(10, "time", 7)
        self.change_event(10, "end_time", 7)
        report = self.run_verifier(1)
        finding = next(item for item in report['findings'] if item['rule'] == 'events.no_skipped_action')
        self.assertEqual((finding['event_sequence'], finding['time'], finding['expected'], finding['observed']),
                         (10, 7, True, False))
        # Keep the actual time legal to isolate the altered duration field next.
        self.change_event(10, 'time', 6)
        report = self.run_verifier(1)
        finding = next(item for item in report['findings'] if item['rule'] == 'operation_complete.end_time')
        self.assertEqual((finding['event_sequence'], finding['expected'], finding['observed']), (10, 6, 7))

    def test_missing_evidence_is_incomplete(self) -> None:
        """Remove each required file in turn and name missing evidence with exit two."""
        for filename in ("scenario.json", "run_manifest.json", "events.jsonl", "dispatch_decisions.jsonl",
                         "summary.json", "final_state.json"):
            with self.subTest(filename=filename):
                path = self.run_directory / filename
                saved = path.read_bytes()
                path.unlink()
                report = self.run_verifier(2)
                self.assertTrue(any(item["category"] == "incomplete" and item["ids"].get("file") == filename
                                    for item in report["findings"]))
                path.write_bytes(saved)

    def test_violation_precedes_missing_evidence(self) -> None:
        """Combine wrong WS1 with absent summary and require FAIL rather than INCOMPLETE."""
        self.change_event(9, "workstation_id", "WS9")
        (self.run_directory / "summary.json").unlink()
        report = self.run_verifier(1)
        self.assertEqual({item["category"] for item in report["findings"]}, {"violation", "incomplete"})

    def test_summary_and_final_state_are_reconciled(self) -> None:
        """Falsify saved wait and terminal count; replay must independently recover the correct values."""
        path = self.run_directory / "summary.json"
        summary = json.loads(path.read_text())
        summary["plant_inventory_wait"] = 99
        path.write_text(json.dumps(summary))
        path = self.run_directory / "final_state.json"
        final = json.loads(path.read_text())
        final["summary"]["remanufactured"] = 0
        path.write_text(json.dumps(final))
        report = self.run_verifier(1)
        self.assertEqual(report["recalculated_summary"]["plant_inventory_wait"], 3)
        finding = next(item for item in report['findings'] if item['rule'] == 'results.summary.json')
        self.assertEqual((finding['expected']['plant_inventory_wait'], finding['observed']['plant_inventory_wait']), (3, 99))
        finding = next(item for item in report['findings'] if item['rule'] == 'results.final_state.json')
        self.assertEqual((finding['expected'], finding['observed']), (1, 0))
        self.assertEqual(finding['ids']['field'], 'results.final_state.json.summary.remanufactured')

    def test_forbidden_quality_input_fails(self) -> None:
        """Inject an individual operation list at the policy boundary and reject the extra information."""
        path = self.run_directory / "dispatch_decisions.jsonl"
        decision = json.loads(path.read_text())
        decision["policy_input"]["requests"][0]["operations"] = [1, 3, 2]
        path.write_text(json.dumps(decision) + "\n")
        report = self.run_verifier(1)
        finding = next(item for item in report['findings'] if item['rule'] == 'results.dispatch_decisions.jsonl')
        expected_request = finding['expected'][0]['policy_input']['requests'][0]
        observed_request = finding['observed'][0]['policy_input']['requests'][0]
        self.assertNotIn('operations', expected_request)
        self.assertEqual(observed_request['operations'], [1, 3, 2])

    def test_fingerprint_and_sequence_tampering_fail(self) -> None:
        """Change the input bytes and duplicate an event ID; both independent checks must fail."""
        path = self.run_directory / "scenario.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.change_event(10, "sequence", 9)
        report = self.run_verifier(1)
        self.assertTrue(any(item["rule"] == "manifest.scenario_sha256" for item in report["findings"]))
        self.assertTrue(any(item["rule"] == "events.sequence" for item in report["findings"]))

    def test_unknown_formats_and_malformed_evidence_are_incomplete(self) -> None:
        """Check unsupported versions, missing event fields, malformed JSON and nonfinite JSON."""
        mutations = [("run_manifest.json", '{"input_format_version": 99, "output_format_version": 99}'),
                     ("events.jsonl", '{"sequence": 1, "event_type": "return_release"}\n'),
                     ("events.jsonl", '{broken json\n'),
                     ("summary.json", '{"plant_inventory_wait": NaN}'),
                     ("summary.json", '{"plant_inventory_wait": 1e999}'),
                     ("run_manifest.json", json.dumps({**json.loads((self.run_directory / "run_manifest.json").read_text()),
                                                        "status": "running"}))]
        for filename, contents in mutations:
            with self.subTest(contents=contents):
                path = self.run_directory / filename
                saved = path.read_bytes()
                path.write_text(contents)
                self.run_verifier(2)
                path.write_bytes(saved)

    def test_verifier_runs_without_simulation_package(self) -> None:
        """Copy only the verifier script and run with isolated Python outside the repository."""
        standalone = Path(self.temporary.name) / "verify_simulation.py"
        shutil.copyfile(ROOT / "tools" / "verify.py", standalone)
        result = subprocess.run([sys.executable, "-I", str(standalone), str(self.run_directory)],
                                cwd=self.temporary.name, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_truncated_horizons_and_zero_distance(self) -> None:
        """Verify retained travel/inventory/processing states and same-time zero-distance legs."""
        for horizon in (0, 1, 7, 9, 13, 14):
            with self.subTest(horizon=horizon):
                scenario = single_product_scenario()
                scenario["horizon"] = horizon
                if horizon == 14:
                    scenario["collection_point"]["coordinates"] = [0, 0]
                config = Path(self.temporary.name) / f"scenario-{horizon}.json"
                config.write_text(json.dumps(scenario))
                self.run_directory = Path(self.temporary.name) / f"run-{horizon}"
                result = subprocess.run([sys.executable, "-m", "simulation", "--resolved-scenario", str(config),
                    "--output", str(self.run_directory)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.run_verifier(0)
