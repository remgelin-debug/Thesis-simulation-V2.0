"""Optional Matplotlib single-run replay: python -m tools.viewer RUN_FOLDER."""
from __future__ import annotations

# main and simulation's optional --visualize call open_viewer. RunReplay reads
# recorded evidence; ReplayController controls display time and exact stepping.
# MatplotlibViewer draws that state and its calendars/processing history. Controls
# never run routing again. Matplotlib is imported only when a viewer is created.

import argparse
from pathlib import Path
import time
from typing import TYPE_CHECKING

from .replay import Record, ReplayController, RunReplay

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.lines import Line2D
    from matplotlib.collections import PolyCollection


PROCESSING_COLOR = '#f28e2b'
PROCESSING_ALPHA = 0.5


# Draw the map, details and workstation timetable

def _product_labels(scenario: Record, state: Record) -> dict[str, str]:
    """Return product labels with the operations still required at this replay position.

    draw_snapshot supplies saved scenario and display state. Use the known destination's
    workstations, or compatible alternatives before assignment. next_operation moves
    only after the completion record, so Previous restores that operation in the label.
    Read only; do not draw or change evidence.
    """
    workstations_by_plant = {
        plant['id']: {station['operation_id']: station['id'] for station in plant['workstations']}
        for plant in scenario['plants']
    }
    workstation_options = {}
    for workstations in workstations_by_plant.values():
        for operation, workstation in workstations.items():
            workstation_options.setdefault(operation, set()).add(workstation)
    unassigned_workstations = {operation: '/'.join(sorted(options))
                               for operation, options in workstation_options.items()}
    labels = {}
    for definition in scenario['products']:
        identifier = definition['id']
        product = state['products'][identifier]
        destination = product['actual_destination'] or product['planned_destination']
        workstations = workstations_by_plant.get(destination, unassigned_workstations)
        remaining = ' → '.join(workstations[operation]
                               for operation in definition['operations'][product['next_operation']:])
        labels[identifier] = f'{identifier} [{remaining}]' if remaining else identifier
    return labels


def draw_snapshot(map_axes: Axes, detail_axes: Axes, scenario: Record, state: Record,
                  policy: str, paused: bool) -> None:
    """Redraw the map and details using saved sites and current display state.

    MatplotlibViewer.redraw supplies axes, scenario, state, policy and pause flag.
    Show vehicle positions/loads, plant waiting inventory and active/remaining work.
    Qualify repeated workstation IDs by plant. Return None; change figure objects
    only. No simulation runs, events or saved files change.
    """
    map_axes.clear()
    detail_axes.clear()
    detail_axes.axis('off')
    product_labels = _product_labels(scenario, state)
    sites = {site['id']: site for site in [*scenario['collection_points'], *scenario['plants'], *scenario['recycling_facilities']]}
    for definitions, marker, color, label in ((scenario['collection_points'], 's', '#2878b5', 'Collection'),
            (scenario['plants'], 's', '#258546', 'Plant'), (scenario['recycling_facilities'], '^', '#aa6500', 'Recycler')):
        for index, site in enumerate(definitions):
            x, y = site['coordinates']
            map_axes.scatter(x, y, marker=marker, s=150, color=color, label=label if index == 0 else None, zorder=3)
            waiting = [product_labels[identifier] for identifier, product in state['products'].items()
                       if product['state'] == 'at_collection_point' and product['location'] == site['id']]
            suffix = '\nAvailable: ' + (', '.join(waiting) or 'none') if label == 'Collection' else ''
            map_axes.annotate(site['id'] + suffix, (x, y), xytext=(6, 12), textcoords='offset points', fontsize=9)
    lines = [policy, '', 'AVs']
    for index, (identifier, vehicle) in enumerate(state['vehicles'].items()):
        x, y = vehicle['position']
        map_axes.scatter(x, y, color='#b82842', s=65, edgecolors='white', zorder=5)
        load = ', '.join(product_labels[product_id] for product_id in vehicle['load']) or 'empty'
        map_axes.annotate(f'{identifier} [{load}]', (x, y), xytext=(6, -20 - index * 13), textcoords='offset points', fontsize=9)
        stops = vehicle['remaining_stops'] or ([{'site_id': vehicle['active_leg']['target']}] if vehicle['active_leg'] else [])
        route = [[x, y], *[sites[stop['site_id']]['coordinates'] for stop in stops]]
        if len(route) > 1:
            map_axes.plot([point[0] for point in route], [point[1] for point in route], '--', color='#b82842', alpha=0.5)
        remaining = 'unlimited' if vehicle['remaining_range'] is None else f"{vehicle['remaining_range']:.6f}"
        lines.extend([f'{identifier}: ({x:.6f}, {y:.6f})', f"  {vehicle['status']}; load: {load}", f'  range: {remaining}'])
        if stops:
            lines.append('  route: ' + ' -> '.join(stop['site_id'] for stop in stops))
    for definition in scenario['plants']:
        plant = state['plants'][definition['id']]
        capacity = 'unlimited' if definition['inventory_capacity'] is None else str(definition['inventory_capacity'])
        lines.extend(['', f"{definition['id']} | inventory capacity {capacity}",
            '  Waiting: ' + (', '.join(product_labels[product_id] for product_id in plant['inventory']) or 'none'),
            '  Held slots: ' + (', '.join(product_labels[product_id] for product_id in plant['held_slots']) or 'none'),
            '  AVs awaiting unload: ' + (', '.join(plant['waiting_vehicles']) or 'none')])
        for station in definition['workstations']:
            job = plant['workstations'][station['id']]
            if job is None:
                opened = any(start <= state['time'] < end for start, end in station['windows'])
                description = 'idle / ' + ('open' if opened else 'closed')
            else:
                description = f"{product_labels[job['product_id']]} op {job['operation_id']} ({job['start_time']:.6f} -> {job['end_time']:.6f})"
            lines.append(f"  {station['id']}: {description}")
    lines.extend(['', 'Released products'])
    lines.extend(f"  {product_labels[identifier]}: {product['state']}" for identifier, product in state['products'].items()
                 if product['state'] != 'unreleased')
    event = state['last_event']
    event_label = 'no events applied' if event is None else f"#{event['sequence']} {event['event_type']}"
    map_axes.set_title(f"Simulation time {state['time']:.9f} | {'PAUSED' if paused else 'RUNNING'}\nAfter {event_label}")
    map_axes.set_xlabel('x (distance units)')
    map_axes.set_ylabel('y (distance units)')
    map_axes.set_aspect('equal', adjustable='datalim')
    map_axes.margins(x=0.25, y=0.5)
    map_axes.grid(alpha=0.2)
    map_axes.legend(loc='lower left', fontsize=8)
    detail_axes.text(0, 1, '\n'.join(lines), va='top', family='monospace', fontsize=max(6, min(9, 300 / len(lines))))


def draw_availability_timetable(axes: Axes, scenario: Record, time: float) -> Line2D:
    """Draw saved workstation windows and return the moving time-marker line.

    MatplotlibViewer.__init__ supplies axes, resolved scenario and current time.
    Use one clearly labelled row per plant/workstation. Generated schedules already
    exist in scenario.json; do not generate them again. Change figure objects only;
    write no files or simulation events.
    """
    from matplotlib.patches import Patch
    axes.clear()
    labels = []
    colors = ('#83b6ce', '#94bd8d', '#b9a4cc')
    for facility_index, plant in enumerate(scenario['plants']):
        first_row = len(labels)
        for station in plant['workstations']:
            row = len(labels)
            labels.append(f"{plant['id']} / {station['id']}")
            axes.broken_barh([(opening, closing - opening) for opening, closing in station['windows']],
                             (row - 0.3, 0.6), facecolors=colors[facility_index % len(colors)], zorder=2)
        if len(labels) > first_row:
            axes.axhspan(first_row - 0.5, len(labels) - 0.5, color=colors[facility_index % len(colors)], alpha=0.1)
            axes.axhline(len(labels) - 0.5, color='#777777', linewidth=0.6)
    axes.set_yticks(range(len(labels)), labels)
    axes.set_ylim(max(len(labels), 1) - 0.5, -0.5)
    axes.set_xlim(0, max(scenario['horizon'], 1))
    axes.set_xlabel('Simulation time', fontsize=9)
    axes.set_title('Workstation timetable\nRed line: viewing time', fontsize=10)
    axes.tick_params(axis='y', labelsize=9, length=0, pad=6)
    axes.tick_params(axis='x', labelsize=9)
    axes.locator_params(axis='x', nbins=4)
    axes.grid(axis='x', alpha=0.2)
    axes.legend(handles=[Patch(facecolor=colors[0], label='Scheduled availability'),
                         Patch(facecolor=PROCESSING_COLOR, alpha=PROCESSING_ALPHA, label='Actual processing')],
                loc='upper left', bbox_to_anchor=(0, -0.16), borderaxespad=0, fontsize=8, frameon=False)
    return axes.axvline(time, color='#b82842', linewidth=1.7, zorder=4)


def draw_processing_bands(axes: Axes, scenario: Record, state: Record) -> list[PolyCollection]:
    """Draw recorded processing over the timetable and return the orange band objects.

    MatplotlibViewer.redraw supplies scenario and the current event-prefix snapshot.
    Show completed jobs and only the elapsed part of active jobs. Use both plant
    and workstation IDs; do not clip processing at scheduled closures. Exact event
    order controls completion/start at the same time. Read snapshot only; no files
    or simulation state change.
    """
    bands = []
    for plant in scenario['plants']:
        recorded = state['plants'][plant['id']]
        for station in plant['workstations']:
            row = len(bands)
            intervals = [(job['start_time'], job['end_time'] - job['start_time'])
                         for job in recorded['processing_history'][station['id']]]
            active = recorded['workstations'][station['id']]
            if active is not None:
                intervals.append((active['start_time'], max(0.0, state['time'] - active['start_time'])))
            bands.append(axes.broken_barh(intervals, (row - 0.21, 0.42), facecolors=PROCESSING_COLOR,
                edgecolors='#c86d10', linewidth=0.5, alpha=PROCESSING_ALPHA, zorder=3))
    return bands


class MatplotlibViewer:
    """Connect replay controls to the map, details, timetable, timer and window."""

    # Create and redraw the figure

    def __init__(self, controller: ReplayController) -> None:
        """Create a paused viewer with map, details, timetable and playback controls.

        open_viewer supplies ReplayController. Import optional Matplotlib here, create
        figure objects and register button/keyboard/timer callbacks. Return None.
        Only viewer/controller objects change. Rendering works with Agg, a backend that
        needs no desktop; show needs a desktop backend. Missing Matplotlib gives a setup
        error. No saved evidence is written.
        """
        try:
            import matplotlib.pyplot as pyplot
            from matplotlib.widgets import Button, TextBox
        except ImportError as exception:
            raise ValueError('Install optional viewer dependencies: python -m pip install -r requirements-visualization.txt') from exception
        self.pyplot, self.controller = pyplot, controller
        self.figure = pyplot.figure(figsize=(18, 10))
        self.map_axes = self.figure.add_axes((0.04, 0.25, 0.31, 0.65))
        self.detail_axes = self.figure.add_axes((0.40, 0.24, 0.31, 0.70))
        self.timetable_axes = self.figure.add_axes((0.79, 0.31, 0.19, 0.56))
        self.timetable_marker = draw_availability_timetable(
            self.timetable_axes, controller.replay.scenario, controller.state['time'])
        self.availability_bands = list(self.timetable_axes.collections)
        self.processing_bands: list[PolyCollection] = []
        self.play_button = Button(self.figure.add_axes((0.06, 0.12, 0.12, 0.045)), 'Resume')
        self.previous_button = Button(self.figure.add_axes((0.20, 0.12, 0.14, 0.045)), 'Previous event')
        self.step_button = Button(self.figure.add_axes((0.35, 0.12, 0.12, 0.045)), 'Next event')
        self.reset_button = Button(self.figure.add_axes((0.48, 0.12, 0.10, 0.045)), 'Restart')
        self.time_box = TextBox(self.figure.add_axes((0.14, 0.045, 0.13, 0.04)), 'Time + Enter ', initial=str(controller.state['time']))
        self.event_box = TextBox(self.figure.add_axes((0.40, 0.045, 0.12, 0.04)), 'Event # + Enter ', initial=str(controller.state['events_applied']))
        self.message = self.figure.text(0.62, 0.13, '', fontsize=9, va='top')
        self.figure.text(0.56, 0.05, 'Space: pause/resume | Left/B: previous | Right/N: next record\nTime seek settles all same-time events; event seek stops after one record.', fontsize=9)
        self.play_button.on_clicked(self.toggle_play)
        self.previous_button.on_clicked(self.step_previous_event)
        self.step_button.on_clicked(self.step_next_event)
        self.reset_button.on_clicked(self.restart)
        self.time_box.on_submit(self.seek_time)
        self.event_box.on_submit(self.seek_event)
        self.figure.canvas.mpl_connect('key_press_event', self.on_key)
        self.figure.canvas.mpl_connect('close_event', self.on_close)
        self.last_wall_time = time.perf_counter()
        self.timer = self.figure.canvas.new_timer(interval=40)
        self.timer.add_callback(self.on_timer)
        self.redraw()

    def redraw(self) -> None:
        """Refresh the figure from controller.state without advancing display time.

        Initialization, controls and timer callbacks call this. Replace map/details and
        old processing bands, move the timetable marker, and update labels.
        Return None; change figure objects only. Read saved records without changes.
        """
        controller = self.controller
        draw_snapshot(self.map_axes, self.detail_axes, controller.replay.scenario, controller.state,
                      controller.replay.manifest['policy'], controller.paused)
        for band in self.processing_bands:
            band.remove()
        self.processing_bands = draw_processing_bands(self.timetable_axes, controller.replay.scenario, controller.state)
        self.timetable_marker.set_xdata([controller.state['time'], controller.state['time']])
        self.play_button.label.set_text('Resume' if controller.paused else 'Pause')
        index = controller.state['events_applied']
        if index < len(controller.replay.events):
            following = controller.replay.events[index]
            self.message.set_text(f"Next #{following['sequence']}: {following['event_type']}\nat t={following['time']:.9f}")
        else:
            self.message.set_text('End of recorded run')
        self.figure.canvas.draw_idle()

    # Button and seek callbacks change only replay controls

    def toggle_play(self, event: object = None) -> None:
        """Handle Resume/Pause or Space; reset elapsed wall time so paused time never counts as travel."""
        self.controller.toggle_play()
        self.last_wall_time = time.perf_counter()
        self.redraw()

    def step_next_event(self, event: object = None) -> None:
        """Handle Next event or Right/N and display exactly one further recorded transition."""
        self.controller.step_next_event()
        self.redraw()

    def step_previous_event(self, event: object = None) -> None:
        """Handle Previous event or Left/B, pausing at the preceding sequence or the initial state."""
        self.controller.step_previous_event()
        self.redraw()

    def restart(self, event: object = None) -> None:
        """Handle Restart by pausing at time zero after all records at that time.

        Call controller.seek_time and redraw. This differs from stepping back before
        the first record. The optional button event is unused; evidence stays unchanged.
        """
        self.controller.seek_time(0)
        self.redraw()

    def seek_time(self, text: str) -> None:
        """Handle Time submission; pause at all-events-through-time state or display an input error."""
        try:
            self.controller.seek_time(float(text))
            self.redraw()
        except ValueError as exception:
            self.message.set_text(str(exception))
            self.figure.canvas.draw_idle()

    def seek_event(self, text: str) -> None:
        """Handle Event submission; pause after an exact sequence number or display an input error."""
        try:
            self.controller.seek_event(int(text))
            self.redraw()
        except ValueError as exception:
            self.message.set_text(str(exception))
            self.figure.canvas.draw_idle()

    # Keyboard, timer and window callbacks

    def on_key(self, event: object) -> None:
        """Handle Space, Left/B and Right/N keyboard shortcuts for replay controls.

        Matplotlib calls this with a key event. Ignore shortcuts while a seek box owns
        typing focus. Delegated controls change the display only, not saved evidence.
        """
        if self.time_box.capturekeystrokes or self.event_box.capturekeystrokes:
            return
        key = getattr(event, 'key', None)
        if key == ' ':
            self.toggle_play()
        elif key in ('right', 'n'):
            self.step_next_event()
        elif key in ('left', 'b'):
            self.step_previous_event()

    def on_timer(self) -> None:
        """Advance replay by elapsed real time and redraw if playback was running.

        Matplotlib's timer calls this. controller.advance uses recorded simulation
        times for motion; frame frequency does not choose physical events.
        Change viewer/controller objects only; no simulation or file writes occur.
        """
        now = time.perf_counter()
        was_running = not self.controller.paused
        self.controller.advance(now - self.last_wall_time)
        self.last_wall_time = now
        if was_running:
            self.redraw()

    def on_close(self, event: object = None) -> None:
        """Stop refresh callbacks when the window closes; saved run evidence is untouched."""
        self.timer.stop()

    # Open, save or release graphical resources

    def show(self) -> None:
        """Open the desktop window and block until it closes.

        open_viewer calls for interactive mode. Start the refresh timer and enter
        Matplotlib's window loop. A non-desktop backend raises a render/setup message.
        No evidence changes; save_image is a separate explicit operation.
        """
        if self.figure.canvas.required_interactive_framework is None:
            raise ValueError('No desktop Matplotlib backend is active. Use TkAgg/QtAgg locally, or --render image.png for headless rendering.')
        self.last_wall_time = time.perf_counter()
        self.timer.start()
        self.pyplot.show(block=True)

    def save_image(self, path: Path) -> None:
        """Save the current figure to the requested PNG, creating parent folders.

        open_viewer calls in render mode. Replace path; require a .png suffix.
        Read replay records and display time unchanged; return None. File errors raise.
        """
        if path.suffix.lower() != '.png':
            raise ValueError('Snapshot output must have a .png suffix')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.figure.savefig(path, dpi=130)

    def close(self) -> None:
        """Stop the timer and release this figure after viewing or rendering.

        open_viewer's finally block calls this even if showing/saving fails.
        Only graphical resources change; saved evidence is untouched.
        """
        self.timer.stop()
        self.pyplot.close(self.figure)


# Open a saved replay or explicitly render a PNG

def open_viewer(run_folder: Path, *, start_time: float = 0.0, event_sequence: int | None = None,
                speed: float = 1.0, render_path: Path | None = None) -> None:
    """Open a paused saved-run replay, or save a PNG without opening a desktop.

    Viewer main and simulation's optional --visualize path call this with folder,
    start time/event, speed and optional render_path. Build RunReplay, its controller
    and MatplotlibViewer. An explicit event overrides the initial time position.
    Return None after closing/releasing the viewer. Only an explicit render writes
    an image; no simulation reruns or evidence changes occur. Matplotlib and readable,
    valid replay records are required.
    """
    if render_path is not None:
        try:
            import matplotlib
        except ImportError as exception:
            raise ValueError('Install optional viewer dependencies: python -m pip install -r requirements-visualization.txt') from exception
        matplotlib.use('Agg')
    replay = RunReplay(run_folder)
    controller = ReplayController(replay, start_time, speed)
    if event_sequence is not None:
        controller.seek_event(event_sequence)
    viewer = MatplotlibViewer(controller)
    try:
        if render_path is None:
            viewer.show()
        else:
            viewer.save_image(render_path)
    finally:
        viewer.close()


# Read terminal arguments

def main() -> int:
    """Read saved-run viewer options and return 0 after the window closes or PNG saves.

    python -m tools.viewer calls this. open_viewer reads evidence and manages the
    figure. Invalid arguments exit 2; replay/viewer errors exit 1 with a message.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder', type=Path)
    start = parser.add_mutually_exclusive_group()
    start.add_argument('--time', type=float, default=0.0)
    start.add_argument('--event', type=int)
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--render', type=Path)
    arguments = parser.parse_args()
    try:
        open_viewer(arguments.run_folder, start_time=arguments.time, event_sequence=arguments.event,
                    speed=arguments.speed, render_path=arguments.render)
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as exception:
        parser.exit(1, f'Viewer failed: {exception}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
