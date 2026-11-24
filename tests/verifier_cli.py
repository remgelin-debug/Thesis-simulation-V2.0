"""Share matching verifier-command assertions across six test modules.

Inventory, dynamic, range, quality and recycling tests call verify_run_via_cli.
It runs the root verifier command and reads its report; scenarios and physical
expectations stay local to each test. This is a test helper, not verification
logic and not a simulation dependency.
"""
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]


def verify_run_via_cli(test_case: unittest.TestCase, directory: Path,
                       expected_exit: int = 0) -> dict[str, Any]:
    """Run the root verifier and return its JSON report after checking both exit codes.

    The six matching test modules supply their unittest instance, temporary run
    folder and expected exit. Keep the current working directory and child options.
    Check process output/exit first, with stdout/stderr on failure; then read the
    report and require its exit_code to agree. The verifier writes reports.
    This helper does not change scenario/events or decide expected physical results.
    """
    result = subprocess.run([sys.executable, str(ROOT / 'verify_simulation.py'), str(directory)],
                            capture_output=True, text=True)
    test_case.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
    report = json.loads((directory / 'verification.json').read_text())
    test_case.assertEqual(report['exit_code'], expected_exit)
    return report
