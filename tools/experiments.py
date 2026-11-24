"""Freeze seeded inputs, launch separate policy runs, verify, and export matched results."""
from __future__ import annotations

# main calls run_experiments or refresh_verification. New batches resolve one
# scenario per condition/replication and reuse its bytes across selected policies.
# Child commands execute simulations and the independent verifier. This tool
# retains attempts and exports only suitable complete groups as paired results.
# Its supported conditions are single-site format two, not the network pilot.

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any

from simulation.config import validate_generation_config
from simulation.generator import prepare_scenario

Record = dict[str, Any]
PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
POLICIES = ('static_quality_unaware', 'static_quality_aware',
            'dynamic_quality_unaware', 'dynamic_quality_aware')
METRICS = ('generated', 'remanufactured', 'recycled', 'unfinished',
           'vehicle_distance', 'plant_inventory_wait', 'waiting_to_unload')
SEED_DERIVATION = 'SHA-256 UTF-8 thesis-experiment-v1|{master_seed}|{replication_id}; unsigned big-endian integer; IDs start at 1'


# Prepare seeds, files and child-process evidence

def derive_replication_seed(master_seed: int, replication_id: int) -> int:
    """Return a repeatable seed for one numbered replication, starting at 1.

    run_experiments calls this. The seed does not depend on condition or policy
    execution order. Invalid integer inputs raise ValueError; nothing is changed.
    """
    if type(master_seed) is not int or type(replication_id) is not int or replication_id < 1:
        raise ValueError('Require an integer master seed and a positive integer replication ID')
    encoded = f'thesis-experiment-v1|{master_seed}|{replication_id}'.encode('utf-8')
    return int.from_bytes(hashlib.sha256(encoded).digest(), 'big')


def write_json(path: Path, value: Record) -> None:
    """Write an experiment JSON export through a temporary file, then replace path.

    Batch execution and export call this. Read value only; write UTF-8 with LF.
    File/JSON errors propagate. This does not write simulation events.
    """
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    temporary.replace(path)


def read_json_object(path: Path) -> Record:
    """Read a JSON dictionary for experiment setup, inspection or refresh.

    Return the parsed object without changing its file. Invalid JSON or a
    non-object raises ValueError; file errors reach the caller.
    """
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def execute_command(command: list[str], log_prefix: Path) -> Record:
    """Run a child command from the project root and save both output streams.

    run_experiments and inspect_run pass argument lists and a log-file prefix.
    Return command, working directory, exit code, launch error and log paths.
    Create log parents and retain stdout/stderr after failure too. A launch error
    has no exit code; it is not successful execution. Child commands may write run
    or verification files. This function does not execute physical events itself.
    """
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        result = subprocess.run(command, cwd=PROJECT_DIRECTORY, capture_output=True, text=True, encoding='utf-8', errors='replace')
        exit_code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except OSError as exception:
        exit_code, stdout, stderr = None, '', str(exception)
        error = str(exception)
    stdout_path, stderr_path = Path(str(log_prefix) + '.stdout.txt'), Path(str(log_prefix) + '.stderr.txt')
    stdout_path.write_text(stdout, encoding='utf-8', newline='\n')
    stderr_path.write_text(stderr, encoding='utf-8', newline='\n')
    return {'command': command, 'working_directory': str(PROJECT_DIRECTORY), 'exit_code': exit_code,
            'launch_error': error, 'stdout_log': str(stdout_path), 'stderr_log': str(stderr_path)}


def validate_experiment_inputs(configuration_paths: list[Path], replications: int,
                               master_seed: int, policies: list[str]) -> list[tuple[Path, bytes, Record]]:
    """Check all batch settings before creating any output folders.

    run_experiments supplies paths, replication count, seed and policy names.
    Return (absolute path, original bytes, parsed configuration) tuples.
    Only single-site format-two generation conditions are supported. Read only;
    invalid settings or unsupported conditions raise. No random draws occur.
    """
    derive_replication_seed(master_seed, 1)
    if type(replications) is not int or replications < 1:
        raise ValueError('Replications must be a positive integer')
    if not configuration_paths or not policies or len(set(policies)) != len(policies) or set(policies) - set(POLICIES):
        raise ValueError('Require configurations and distinct supported policies')
    configurations = []
    for path in configuration_paths:
        path = path.resolve()
        configuration = read_json_object(path)
        validate_generation_config(configuration)
        configurations.append((path, path.read_bytes(), configuration))
    return configurations


# Record and inspect each attempted policy run

def make_run_record(condition_id: str, replication_id: int, master_seed: int,
                    derived_seed: int, policy: str, scenario_path: Path, root: Path) -> Record:
    """Return a pending attempt record with the exact simulation command to rerun.

    run_experiments passes condition/replication IDs, seeds, policy and paths.
    Build relative manifest paths and command arguments without creating files or
    changing inputs. inspect_run adds verifier details after execution.
    """
    directory = scenario_path.parent / 'runs' / policy
    return {'condition_id': condition_id, 'replication_id': replication_id, 'master_seed': master_seed,
        'derived_seed': derived_seed, 'policy': policy, 'resolved_scenario': str(scenario_path.relative_to(root)),
        'run_directory': str(directory.relative_to(root)), 'scenario_sha256': None, 'stream_seeds': {},
        'simulation': {'status': 'PENDING', 'command': [sys.executable, '-m', 'simulation',
            '--resolved-scenario', str(scenario_path), '--policy', policy, '--output', str(directory)]},
        'verification': {'status': 'NOT_RUN', 'exit_code': None}, 'summary': None,
        'scenario_hash_matches': False, 'included_in_verified_comparison': False}


def inspect_run(root: Path, record: Record, verify: bool = True) -> None:
    """Inspect one saved attempt and optionally run fresh independent verification.

    run_experiments and refresh_verification pass root and a changing manifest
    record. Check frozen/run hashes and read totals. With verify=True, launch the
    standalone verifier, read its fresh report and check report/exit agreement.
    Update the attempt with status, errors and summary; return None. Reports and
    child logs may be written, but scenario/events stay untouched. Failed or missing
    evidence stays visible and cannot enter verified comparisons.
    """
    directory = root / record['run_directory']
    scenario_path = root / record['resolved_scenario']
    record.update(scenario_hash_matches=False, observed_scenario_sha256=None, summary=None, inspection_error=None)
    try:
        frozen_hash = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
        observed_hash = hashlib.sha256((directory / 'scenario.json').read_bytes()).hexdigest()
        record['observed_scenario_sha256'] = observed_hash
        record['scenario_hash_matches'] = frozen_hash == observed_hash == record['scenario_sha256']
        saved_summary = read_json_object(directory / 'summary.json')
        record['summary'] = {metric: saved_summary.get(metric) if type(saved_summary.get(metric)) in (int, float)
            and math.isfinite(saved_summary[metric]) else None for metric in METRICS}
    except (OSError, ValueError) as exception:
        record['inspection_error'] = str(exception)
    if not verify:
        record['verification'] = {'status': 'NOT_RUN', 'exit_code': None, 'reason': 'Simulation did not complete' if record['simulation']['status'] != 'COMPLETED' else 'Verification disabled explicitly'}
        return
    command = [sys.executable, str(PROJECT_DIRECTORY / 'tools' / 'verify.py'), str(directory)]
    process = execute_command(command, scenario_path.parent / 'logs' / f"{record['policy']}.verification")
    verification = {**process, 'status': 'ERROR', 'report_path': str((directory / 'verification.json').relative_to(root))}
    try:
        report = read_json_object(directory / 'verification.json')
        expected = {0: 'PASS', 1: 'FAIL', 2: 'INCOMPLETE'}.get(process['exit_code'])
        if expected is None or report.get('exit_code') != process['exit_code'] or report.get('status') != expected:
            raise ValueError('Verifier exit code and saved report disagree or child did not complete')
        verification.update(status=expected, findings=report.get('findings', []))
        if expected == 'PASS':
            summary = report.get('recalculated_summary')
            if not isinstance(summary, dict) or any(type(summary.get(metric)) not in (int, float)
                    or not math.isfinite(summary[metric]) for metric in METRICS):
                raise ValueError('PASS report lacks finite independently recalculated metrics')
            record['summary'] = summary
    except (OSError, ValueError) as exception:
        verification.update(status='ERROR', error=str(exception))
    record['verification'] = verification


# Select eligible matched groups and export results

def build_comparisons(manifest: Record) -> list[Record]:
    """Return comparison groups and mark which attempts may enter paired results.

    export_experiment supplies the manifest. Each condition/replication needs all
    selected policies completed, hash-matched and PASS. Name missing policies when
    that is not true. Update only each attempt's inclusion flag; write no files.
    """
    comparisons = []
    for condition in manifest['conditions']:
        for replication_id in range(1, manifest['replication_count'] + 1):
            members = [record for record in manifest['runs'] if record['condition_id'] == condition['condition_id']
                       and record['replication_id'] == replication_id]
            eligible = [record for record in members if record['simulation']['status'] == 'COMPLETED'
                        and record['verification']['status'] == 'PASS' and record['scenario_hash_matches']]
            verified_policies = [record['policy'] for record in eligible]
            missing = [policy for policy in manifest['policies'] if policy not in verified_policies]
            hashes = {record.get('observed_scenario_sha256') for record in members}
            matching_hashes = len(members) == len(manifest['policies']) and len(hashes) == 1 and all(
                record['scenario_hash_matches'] for record in members)
            complete = not missing and matching_hashes
            for record in members:
                record['included_in_verified_comparison'] = complete
            comparisons.append({'condition_id': condition['condition_id'], 'replication_id': replication_id,
                'complete': complete, 'verified_policies': verified_policies, 'missing_policies': missing,
                'matching_scenario_hashes': matching_hashes,
                'exclusions': {record['policy']: {'simulation_status': record['simulation']['status'],
                    'verification_status': record['verification']['status'], 'scenario_hash_matches': record['scenario_hash_matches']}
                    for record in members if record['policy'] in missing}})
    return comparisons


def write_csv(path: Path, fieldnames: list[str], rows: list[Record]) -> None:
    """Write explicit experiment columns and rows as UTF-8 CSV with LF endings.

    export_experiment supplies path, header order and rows. Replace the export;
    leave row data unchanged. Parents must exist; writing errors propagate.
    """
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def summarize_policy_runs(condition_id: str, policy: str, group: list[Record]) -> Record:
    """Return counts and metric statistics for one condition and policy.

    export_experiment passes rows in attempt order. Count every attempt; means and
    sample standard deviations use only complete verified policy groups.
    No samples gives None; one sample has no defined sample standard deviation.
    Read rows only and return a new dictionary in CSV order; no files change.
    """
    paired = [row for row in group if row['included_in_verified_comparison']]
    summary = {'condition_id': condition_id, 'policy': policy, 'attempted_runs': len(group),
        'verified_runs': sum(row['verification_status'] == 'PASS' and row['simulation_status'] == 'COMPLETED'
            and row['scenario_hash_matches'] for row in group), 'matched_replications': len(paired),
        'excluded_replications': len(group) - len(paired)}
    for metric in METRICS:
        values = [row[metric] for row in paired]
        summary[metric + '_mean'] = statistics.mean(values) if values else None
        summary[metric + '_sample_stdev'] = statistics.stdev(values) if len(values) > 1 else None
    return summary


def export_experiment(root: Path, manifest: Record) -> None:
    """Rebuild comparisons and save the manifest and experiment CSV files.

    run_experiments and refresh_verification pass the root and manifest. Update
    comparisons through build_comparisons. Raw results keep every attempt; paired
    results and summaries use complete verified groups with replication IDs.
    Write results.csv, paired_results.csv, comparisons.csv, summaries.csv and
    experiment_manifest.json, replacing previous exports. Return None; do not change
    simulation evidence or record physical events.
    """
    manifest['comparisons'] = build_comparisons(manifest)
    rows = []
    for record in manifest['runs']:
        rows.append({key: record[key] for key in ('condition_id', 'replication_id', 'master_seed', 'derived_seed',
            'policy', 'scenario_sha256', 'run_directory', 'scenario_hash_matches', 'included_in_verified_comparison')})
        rows[-1].update(simulation_status=record['simulation']['status'], verification_status=record['verification']['status'],
                       **{metric: (record['summary'] or {}).get(metric) for metric in METRICS})
    fields = ['condition_id', 'replication_id', 'master_seed', 'derived_seed', 'policy', 'scenario_sha256',
        'run_directory', 'simulation_status', 'verification_status', 'scenario_hash_matches',
        'included_in_verified_comparison', *METRICS]
    write_csv(root / 'results.csv', fields, rows)
    write_csv(root / 'paired_results.csv', fields, [row for row in rows if row['included_in_verified_comparison']])
    comparisons = [{key: comparison[key] for key in ('condition_id', 'replication_id', 'complete', 'matching_scenario_hashes')}
        | {'missing_policies': ';'.join(comparison['missing_policies'])} for comparison in manifest['comparisons']]
    write_csv(root / 'comparisons.csv', ['condition_id', 'replication_id', 'complete', 'matching_scenario_hashes', 'missing_policies'], comparisons)
    summaries = []
    for condition in manifest['conditions']:
        for policy in manifest['policies']:
            group = [row for row in rows if row['condition_id'] == condition['condition_id'] and row['policy'] == policy]
            summaries.append(summarize_policy_runs(condition['condition_id'], policy, group))
    write_csv(root / 'summaries.csv', ['condition_id', 'policy', 'attempted_runs', 'verified_runs',
        'matched_replications', 'excluded_replications', *[metric + suffix for metric in METRICS
        for suffix in ('_mean', '_sample_stdev')]], summaries)
    write_json(root / 'experiment_manifest.json', manifest)


# Execute new batches or refresh existing evidence

def run_experiments(configuration_paths: list[Path], replications: int, master_seed: int,
                    output_directory: Path, policies: list[str] | None = None, verify: bool = True) -> Record:
    """Generate shared inputs once per replication, then run each selected policy.

    main supplies single-site format-two conditions, count, seed, a new output root
    and optional policies/verification flag. Return the experiment manifest.
    Save each configuration and resolved scenario; every policy reads those exact
    scenario bytes in a separate process/folder. Save attempts, logs and exports
    after each run. Retain whatever evidence failed children produced; this does
    not guarantee complete files after a mid-engine failure. Exclude unsuitable
    attempts from paired comparisons. Existing folders or invalid setup raise.
    """
    policies = list(POLICIES) if policies is None else list(policies)
    configurations = validate_experiment_inputs(configuration_paths, replications, master_seed, policies)
    root = output_directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest: Record = {'format_version': 1, 'master_seed': master_seed, 'replication_count': replications,
        'seed_derivation': SEED_DERIVATION, 'policies': policies, 'verification_enabled': verify,
        'python_executable': sys.executable, 'python_version': sys.version, 'project_directory': str(PROJECT_DIRECTORY),
        'policy_randomness': 'none: deterministic policies and stable tie-breaks',
        'calendars_and_network': 'configured, not randomly generated; frozen in each scenario',
        'conditions': [], 'runs': [], 'status': 'running'}
    for condition_index, (source_path, source_bytes, configuration) in enumerate(configurations, 1):
        condition_id = f'C{condition_index:03d}'
        directory = root / 'conditions' / condition_id
        directory.mkdir(parents=True)
        saved_configuration = directory / 'configuration.json'
        saved_configuration.write_bytes(source_bytes)
        manifest['conditions'].append({'condition_id': condition_id, 'name': source_path.stem,
            'source_configuration': str(source_path), 'saved_configuration': str(saved_configuration.relative_to(root)),
            'configuration_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'generation_settings': configuration['generation'], 'quality_classes': configuration['quality_classes'],
            'collection_point': configuration['collection_point'],
            'recycling_facilities': configuration['recycling_facilities']})
        for replication_id in range(1, replications + 1):
            derived_seed = derive_replication_seed(master_seed, replication_id)
            scenario_path = directory / f'replication_{replication_id:04d}' / 'scenario.json'
            scenario_path.parent.mkdir()
            records = [make_run_record(condition_id, replication_id, master_seed, derived_seed, policy, scenario_path, root)
                       for policy in policies]
            manifest['runs'].extend(records)
            write_json(root / 'experiment_manifest.json', manifest)
            try:
                scenario, scenario_bytes = prepare_scenario(saved_configuration, derived_seed)
                scenario_path.write_bytes(scenario_bytes)
                fingerprint = hashlib.sha256(scenario_bytes).hexdigest()
            except (OSError, ValueError, KeyError, TypeError, OverflowError) as exception:
                for record in records:
                    record['simulation'] = {**record['simulation'], 'status': 'NOT_RUN', 'generation_error': str(exception)}
                export_experiment(root, manifest)
                continue
            for record in records:
                record.update(scenario_sha256=fingerprint, stream_seeds=scenario['seed_provenance']['stream_seeds'],
                              generation_provenance=scenario['seed_provenance'])
                record['simulation']['status'] = 'RUNNING'
                write_json(root / 'experiment_manifest.json', manifest)
                process = execute_command(record['simulation']['command'], scenario_path.parent / 'logs' / f"{record['policy']}.simulation")
                record['simulation'] = {**process, 'status': 'COMPLETED' if process['exit_code'] == 0 else 'FAILED'}
                inspect_run(root, record, verify=verify and process['exit_code'] == 0)
                export_experiment(root, manifest)
    manifest['status'] = 'completed' if all(comparison['complete'] for comparison in manifest['comparisons']) else 'completed_with_exclusions'
    export_experiment(root, manifest)
    return manifest


def refresh_verification(output_directory: Path) -> Record:
    """Recheck saved runs and refresh comparisons without rerunning simulations.

    main refresh mode supplies an existing experiment root. Read its manifest,
    call inspect_run for each attempt and replace verification reports, logs,
    manifest and CSV exports. Return the updated manifest. Inputs/events stay
    unchanged; unsupported or missing experiment metadata raises.
    """
    root = output_directory.resolve()
    manifest = read_json_object(root / 'experiment_manifest.json')
    if manifest.get('format_version') != 1:
        raise ValueError('Unsupported experiment manifest version')
    manifest['verification_enabled'] = True
    for record in manifest['runs']:
        inspect_run(root, record)
    export_experiment(root, manifest)
    manifest['status'] = 'completed' if all(comparison['complete'] for comparison in manifest['comparisons']) else 'completed_with_exclusions'
    write_json(root / 'experiment_manifest.json', manifest)
    return manifest


# Read terminal arguments

def main() -> int:
    """Read experiment options and return 0 for success, 1 for exclusions, 2 for setup errors.

    python -m tools.experiments and run_experiments.py call this. Choose
    run_experiments or refresh_verification, which perform the file writes.
    Refresh accepts only an existing output folder. No physical events execute here.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, nargs='+', action='extend')
    parser.add_argument('--replications', type=int)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--policies', choices=POLICIES, nargs='+')
    parser.add_argument('--no-verify', action='store_true')
    parser.add_argument('--refresh-verification', action='store_true')
    arguments = parser.parse_args()
    if arguments.refresh_verification:
        if arguments.config is not None or arguments.replications is not None or arguments.seed is not None or arguments.policies is not None or arguments.no_verify:
            parser.error('--refresh-verification takes only --output')
    elif arguments.config is None or arguments.replications is None or arguments.seed is None:
        parser.error('Batch execution requires --config, --replications and --seed')
    try:
        manifest = refresh_verification(arguments.output) if arguments.refresh_verification else run_experiments(
            arguments.config, arguments.replications, arguments.seed, arguments.output, arguments.policies, not arguments.no_verify)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exception:
        print(f'Experiment setup failed: {exception}', file=sys.stderr)
        return 2
    complete = sum(comparison['complete'] for comparison in manifest['comparisons'])
    print(f"{len(manifest['runs'])} runs; {complete}/{len(manifest['comparisons'])} complete verified comparisons; {manifest['status']}")
    if not manifest['verification_enabled']:
        return 0 if all(record['simulation']['status'] == 'COMPLETED' and record['scenario_hash_matches'] for record in manifest['runs']) else 1
    return 0 if manifest['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
