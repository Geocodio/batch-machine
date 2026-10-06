import json
import math
import os
import time

import h3

DEFAULT_INTERVAL = 15.0
CELL_RESOLUTION = 6
OUTCOME_INDEX = {'ok': 0, 'error': 2}


class Reporter:
    ''' Prints machine-readable progress lines to stdout.

        Each line is a JSON object starting with {"progress": and is written
        outside the logging module. An interval of 0 disables all output.
    '''

    def __init__(self, interval=None, clock=time.monotonic):
        self._interval = interval
        self._clock = clock
        self._phase = None

    @property
    def interval(self):
        if self._interval is not None:
            return self._interval
        try:
            return float(os.environ.get('OPENADDR_PROGRESS_INTERVAL', DEFAULT_INTERVAL))
        except ValueError:
            return DEFAULT_INTERVAL

    def start_phase(self, name, total=None, unit='features', index=None, count=None):
        interval = self.interval
        if interval <= 0:
            self._phase = None
            return
        self._phase = {
            'phase': name, 'phase_index': index, 'phase_count': count,
            'done': 0, 'total': total, 'unit': unit,
            'counters': {}, 'cells': {}, 'interval': interval,
        }
        self._emit()

    def advance(self, n=1):
        if self._phase:
            self._phase['done'] += n
            self._emit_if_due()

    def add_total(self, n):
        if self._phase:
            self._phase['total'] = (self._phase['total'] or 0) + n

    def set_done(self, done):
        if self._phase:
            self._phase['done'] = done
            self._emit_if_due()

    def count(self, counter_name, n=1):
        if self._phase:
            counters = self._phase['counters']
            counters[counter_name] = counters.get(counter_name, 0) + n

    def cell(self, lon, lat, outcome):
        if not self._phase:
            return
        try:
            if not (math.isfinite(lon) and math.isfinite(lat)):
                return
            cell_id = h3.latlng_to_cell(lat, lon, CELL_RESOLUTION)
        except (TypeError, ValueError, h3.H3BaseException):
            return
        self._phase['cells'].setdefault(cell_id, [0, 0, 0])[OUTCOME_INDEX[outcome]] += 1

    def end_phase(self):
        if self._phase:
            self._emit()
            self._phase = None

    def _emit_if_due(self):
        if self._clock() - self._phase['last_emit'] >= self._phase['interval']:
            self._emit()

    def _emit(self):
        phase = self._phase
        report = {'phase': phase['phase']}
        for key in ('phase_index', 'phase_count'):
            if phase[key] is not None:
                report[key] = phase[key]
        report.update(done=phase['done'], total=phase['total'], unit=phase['unit'])
        if phase['counters']:
            report['counters'] = phase['counters']
        if phase['cells']:
            report['cells'] = phase['cells']
            phase['cells'] = {}
        phase['last_emit'] = self._clock()
        print(json.dumps({'progress': report}, separators=(',', ':')), flush=True)


reporter = Reporter()
