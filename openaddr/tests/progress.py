import io
import json
import csv
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from .. import SourceConfig, progress
from ..conform import GEOM_FIELDNAME, transform_to_out_geojson
from ..progress import Reporter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def run(reporter_fn, interval=15, clock=None):
    reporter = Reporter(interval=interval, clock=clock or FakeClock())
    out = io.StringIO()
    with redirect_stdout(out):
        reporter_fn(reporter)
    return [json.loads(line)['progress'] for line in out.getvalue().splitlines()], out.getvalue()


class TestProgressReporter(unittest.TestCase):

    def test_line_shape(self):
        def script(r):
            r.start_phase('Downloading', total=10, unit='bytes', index=1, count=3)
            r.advance(4)
            r.count('skipped', 2)
            r.end_phase()

        lines, raw = run(script)
        self.assertTrue(all(line.startswith('{"progress":') for line in raw.splitlines()))
        self.assertNotIn(' ', raw.splitlines()[0])
        self.assertEqual(lines[0], {'phase': 'Downloading', 'phase_index': 1, 'phase_count': 3,
                                    'done': 0, 'total': 10, 'unit': 'bytes'})
        self.assertEqual(lines[-1]['done'], 4)
        self.assertEqual(lines[-1]['counters'], {'skipped': 2})

    def test_unknown_total_and_no_index(self):
        lines, _ = run(lambda r: (r.start_phase('Reading source'), r.end_phase()))
        self.assertIsNone(lines[0]['total'])
        self.assertNotIn('phase_index', lines[0])
        self.assertEqual(lines[0]['unit'], 'features')

    def test_throttle(self):
        clock = FakeClock()

        def script(r):
            r.start_phase('p')
            for _ in range(5):
                clock.now += 4
                r.advance()
            r.end_phase()

        lines, _ = run(script, clock=clock)
        self.assertEqual([line['done'] for line in lines], [0, 4, 5])

    def test_cell_deltas_reset_after_each_line(self):
        clock = FakeClock()

        def script(r):
            r.start_phase('p')
            r.cell(-77.0, 38.9, 'ok')
            r.cell(-77.0, 38.9, 'ok')
            r.cell(-77.0, 38.9, 'error')
            clock.now += 20
            r.advance()
            r.cell(-77.0, 38.9, 'ok')
            r.end_phase()

        lines, _ = run(script, clock=clock)
        self.assertNotIn('cells', lines[0])
        (cell_id, counts), = lines[1]['cells'].items()
        self.assertEqual(len(cell_id), 15)
        self.assertEqual(counts, [2, 0, 1])
        self.assertEqual(lines[2]['cells'], {cell_id: [1, 0, 0]})

    def test_disabled_when_interval_is_zero(self):
        def script(r):
            r.start_phase('p', total=1)
            r.advance()
            r.cell(1.0, 1.0, 'ok')
            r.end_phase()

        _, raw = run(script, interval=0)
        self.assertEqual(raw, '')

    def test_environment_controls_interval(self):
        with patch.dict(os.environ, {'OPENADDR_PROGRESS_INTERVAL': '0'}):
            self.assertEqual(Reporter().interval, 0)
        with patch.dict(os.environ, {'OPENADDR_PROGRESS_INTERVAL': '3'}):
            self.assertEqual(Reporter().interval, 3)

    def test_nonfinite_coordinates_are_ignored(self):
        def script(r):
            r.start_phase('p')
            r.cell(float('nan'), 1.0, 'ok')
            r.cell(1.0, float('inf'), 'error')
            r.cell(None, 1.0, 'ok')
            r.end_phase()

        lines, _ = run(script)
        self.assertNotIn('cells', lines[-1])


class TestProgressCallSites(unittest.TestCase):

    def test_transform_emits_first_and_final_line(self):
        config = SourceConfig({
            'schema': 2,
            'layers': {'addresses': [{'name': 'default', 'conform': {'street': 's', 'number': 'n'}}]},
        }, 'addresses', 'default')
        workdir = tempfile.mkdtemp(prefix='testProgress-')
        try:
            extract_path = os.path.join(workdir, 'extract.csv')
            with open(extract_path, 'w', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['s', 'n', GEOM_FIELDNAME])
                writer.writeheader()
                for number in range(3):
                    writer.writerow({'s': 'DESMOND ST', 'n': str(number), GEOM_FIELDNAME: 'POINT (-76.51522 41.97925)'})

            reporter = Reporter(interval=15)
            out = io.StringIO()
            with patch.object(progress, 'reporter', reporter), redirect_stdout(out):
                transform_to_out_geojson(config, extract_path, os.path.join(workdir, 'out.geojson'), 3)
        finally:
            shutil.rmtree(workdir)

        lines = [json.loads(line)['progress'] for line in out.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0]['phase'], lines[0]['done'], lines[0]['total']), ('Mapping columns', 0, 3))
        self.assertEqual((lines[1]['done'], lines[1]['total'], lines[1]['unit']), (3, 3, 'rows'))
        self.assertEqual(list(lines[1]['cells'].values()), [[3, 0, 0]])
