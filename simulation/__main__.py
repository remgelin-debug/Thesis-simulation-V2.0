"""Run one simulation from the terminal with python -m simulation.

main reads the options and calls runner.run_once to prepare, execute and save the
run. It imports tools.viewer only when viewing is requested. A headless run does
not need Matplotlib.
"""
import argparse
import json
from pathlib import Path
from .runner import run_once


def main() -> int:
    """Read terminal options, save one run, and open its replay if requested.

    Called by python -m simulation. Pass the paths, seed and policy to run_once;
    print its summary and return 0 on success. run_once writes the run files.
    The optional viewer reads those completed files; a viewer error leaves them intact.
    Invalid options exit 2. Execution or viewer errors exit 1 with a message.
    This function does not record physical events itself.
    """
    parser = argparse.ArgumentParser(description="Resolved or seeded remanufacturing simulation")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--resolved-scenario", type=Path)
    source.add_argument("--config", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--policy", choices=["static_quality_unaware", "static_quality_aware", "dynamic_quality_unaware", "dynamic_quality_aware"], default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--visualize', action='store_true', help='Open a paused viewer after saving the single run')
    view_start = parser.add_mutually_exclusive_group()
    view_start.add_argument('--view-time', type=float)
    view_start.add_argument('--view-event', type=int)
    parser.add_argument('--view-speed', type=float)
    parser.add_argument('--render', type=Path, help='With --visualize, save an Agg PNG instead of opening a window')
    arguments = parser.parse_args()
    if not arguments.visualize and any(value is not None for value in
            (arguments.view_time, arguments.view_event, arguments.view_speed, arguments.render)):
        parser.error('Viewer options require --visualize')
    if arguments.config is not None and arguments.seed is None:
        parser.error('--config requires an explicit --seed')
    if arguments.resolved_scenario is not None and arguments.seed is not None:
        parser.error('--resolved-scenario is authoritative; do not override its seed')
    try:
        summary = run_once(arguments.config or arguments.resolved_scenario, arguments.output, seed=arguments.seed, policy=arguments.policy)
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        parser.exit(1, f"Simulation failed: {error}\n")
    print(json.dumps(summary, indent=2))
    if arguments.visualize:
        try:
            from tools.viewer import open_viewer
            open_viewer(arguments.output, start_time=arguments.view_time if arguments.view_time is not None else 0.0,
                        event_sequence=arguments.view_event,
                        speed=arguments.view_speed if arguments.view_speed is not None else 1.0, render_path=arguments.render)
        except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as error:
            parser.exit(1, f'Viewer failed; run artifacts remain in {arguments.output}: {error}\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
