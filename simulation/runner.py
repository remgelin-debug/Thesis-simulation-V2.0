"""Prepare, execute and save one run; keep file writing outside the event loop.

simulation.__main__.main calls run_once. It uses config for saved inputs or
generator for new ones, then chooses an engine entry point. State/summary helpers
supply final output. Original scenario bytes are kept for their hash.
Engine/plant/transport record events; this module writes those records to disk.
"""
import copy
import hashlib
import json
from pathlib import Path
from .config import load_scenario, as_dynamic_scenario
from .state import Record, inventory_summary, physical_snapshot
from .generator import prepare_scenario
from .engine import run_inventory_simulation, run_dynamic_simulation


# Write run exports

def write_json(path: Path, value: object) -> None:
    """Write readable UTF-8 JSON with a final newline, replacing path if it exists.

    run_once uses this for the manifest, summary and final state. Read value only;
    JSON or file errors propagate. Parents must already exist.
    """
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


# Prepare, execute and save one run

def run_once(scenario_path: Path, output_directory: Path, *, seed: int | None = None, policy: str | None = None) -> Record:
    """Prepare or load one scenario, execute it, save a new folder and return summary.

    The CLI supplies input/output paths, optional generation seed and policy override.
    Experiments invoke that CLI in child processes. Load original bytes or generate
    resolved bytes, then make a separate execution scenario for the selected policy.
    Create scenario.json and a running manifest before execution. On success write
    events/decisions in their recorded order, summary and final state, then mark the
    manifest completed. Existing output folders are refused. Inputs stay unchanged.
    Engines record events; this function writes them. Invalid scope, inputs, execution
    or file operations raise; no verifier or automatic KPI export is run here.
    """
    # Keep saved input bytes separate from the execution policy override.
    scenario, scenario_bytes = load_scenario(scenario_path) if seed is None else prepare_scenario(scenario_path, seed)
    selected_policy = scenario['policy'] if policy is None else policy
    if selected_policy not in ('static_quality_unaware', 'static_quality_aware', 'dynamic_quality_unaware', 'dynamic_quality_aware'):
        raise ValueError('Selected policy is unsupported for this scenario format')
    if scenario['format_version'] == 3 and selected_policy not in ('dynamic_quality_unaware', 'dynamic_quality_aware'):
        raise ValueError('Format three requires a supported dynamic policy')
    execution_scenario = as_dynamic_scenario(scenario) if selected_policy in ('dynamic_quality_unaware', 'dynamic_quality_aware') else copy.deepcopy(scenario)
    execution_scenario['policy'] = selected_policy
    # Refuse an existing folder so previous evidence cannot be overwritten.
    output_directory.mkdir(parents=True, exist_ok=False)
    fingerprint = hashlib.sha256(scenario_bytes).hexdigest()
    manifest = {"run_id": output_directory.name, "policy": selected_policy,
        "input_format_version": scenario["format_version"], "output_format_version": execution_scenario["format_version"],
        "scenario_sha256": fingerprint, "seed_provenance": scenario["seed_provenance"],
        "status": "running"}
    (output_directory / "scenario.json").write_bytes(scenario_bytes)
    write_json(output_directory / "run_manifest.json", manifest)
    if selected_policy in ('dynamic_quality_unaware', 'dynamic_quality_aware'):
        state = run_dynamic_simulation(execution_scenario)
    else:
        state = run_inventory_simulation(execution_scenario)
    # Execution has finished; save logs without changing record or field order.
    for filename, records in (("events.jsonl", state.events), ("dispatch_decisions.jsonl", state.decisions)):
        with (output_directory / filename).open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
    final_state = physical_snapshot(state, released_only=False)
    summary = inventory_summary(state)
    final_state["summary"] = summary
    write_json(output_directory / "final_state.json", final_state)
    write_json(output_directory / "summary.json", summary)
    manifest["status"] = "completed"
    write_json(output_directory / "run_manifest.json", manifest)
    return summary
