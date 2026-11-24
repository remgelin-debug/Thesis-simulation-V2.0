"""Check supported commands, optional graphics and standalone tools.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from simulation.runner import run_once
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json'
PILOT = ROOT / 'configs' / 'three_site_pilot.json'


class RefactorTests(unittest.TestCase):
    """Exercise new boundaries through real runs; all generated files stay temporary."""

    # Check the expected model or evidence behavior

    def test_only_current_pilot_is_user_facing(self) -> None:
        """Guard the requested config boundary without depending on local results or Git history."""
        self.assertEqual([path.name for path in sorted((ROOT / 'configs').glob('*.json'))],
                         ['three_site_pilot.json'])

    def test_full_cli_without_optional_plotting(self) -> None:
        """Run the CLI with plotting blocked; return no value and clean temporary evidence automatically."""
        # Fixed execution remains importable via runner, but no plotting code may load.
        program = '''
import importlib.abc, runpy, sys
class NoPlotting(importlib.abc.MetaPathFinder):
    """Block optional graphical dependencies in the CLI child process."""
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        """Reject plotting imports; otherwise let normal import resolution continue."""
        if fullname.split('.')[0] in ('matplotlib', 'numpy', 'tkinter'):
            raise ImportError('Optional plotting blocked by acceptance check: ' + fullname)
sys.meta_path.insert(0, NoPlotting())
sys.argv = ['simulation', '--resolved-scenario', sys.argv[1], '--output', sys.argv[2]]
runpy.run_module('simulation', run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'run'
            result = subprocess.run([sys.executable, '-c', program, str(FIXTURE), str(folder)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(verify_run(folder)['status'], 'PASS')

    def test_tool_commands_and_compatibility_wrappers(self) -> None:
        """Check supported simulation/tools and retained root CLI wrappers without creating runs or opening a GUI."""
        commands = [
            ['-m', 'tools.verify'], ['verify_simulation.py'],
            ['-m', 'tools.kpis'], ['export_simulation_kpis.py'],
            ['-m', 'tools.experiments'], ['run_experiments.py'],
            ['-m', 'tools.viewer'], ['-m', 'simulation'],
        ]
        for arguments in commands:
            with self.subTest(command=arguments):
                result = subprocess.run([sys.executable, *arguments, '--help'], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
                if arguments == ['-m', 'simulation']:
                    self.assertIn('--visualize', result.stdout)

    def test_verifier_and_kpis_stay_standalone(self) -> None:
        """Copy each independent tool outside the package and run it in isolation on temporary evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_once(FIXTURE, root / 'run')
            for name, arguments in [('verify', [str(root / 'run')]),
                                    ('kpis', [str(root / 'run'), '--output', str(root / 'kpis.csv')])]:
                standalone = root / (name + '.py')
                shutil.copyfile(ROOT / 'tools' / (name + '.py'), standalone)
                result = subprocess.run([sys.executable, '-I', str(standalone), *arguments],
                                        cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / 'kpis.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(json.loads((root / 'run' / 'verification.json').read_text())['status'], 'PASS')

    def test_pilot_repeat_and_policy_reuse_preserve_input_bytes(self) -> None:
        """Resolve seed 42 twice and run both dynamic policies; temporary outputs are the only mutations."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('first', 'repeat'):
                run_once(PILOT, root / name, seed=42, policy='dynamic_quality_aware')
            run_once(root / 'first' / 'scenario.json', root / 'unaware', policy='dynamic_quality_unaware')
            for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
                self.assertEqual((root / 'first' / filename).read_bytes(), (root / 'repeat' / filename).read_bytes())
            expected = hashlib.sha256((root / 'first' / 'scenario.json').read_bytes()).hexdigest()
            for name in ('first', 'repeat', 'unaware'):
                self.assertEqual(json.loads((root / name / 'run_manifest.json').read_text())['scenario_sha256'], expected)
                self.assertEqual(hashlib.sha256((root / name / 'scenario.json').read_bytes()).hexdigest(), expected)
                self.assertEqual(verify_run(root / name)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
