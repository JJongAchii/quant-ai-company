"""Cold-start registration must not concurrently mutate the calendar's holiday state."""

import subprocess
import sys


def test_parallel_cold_start_registration_uses_safe_calendar_construction():
    # A fresh interpreter matters: the normal suite warms these global caches
    # during collection, hiding the race observed after the production restart.
    code = '''
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock
from time import sleep
import exchange_calendars
from quant_company.briefing import schedule

factory = exchange_calendars.get_calendar
counter = Lock()
active = peak = 0
def observed(*args, **kwargs):
    global active, peak
    with counter:
        active += 1
        peak = max(peak, active)
    try:
        sleep(0.01)  # Give concurrent cold callers an opportunity to overlap.
        return factory(*args, **kwargs)
    finally:
        with counter:
            active -= 1
exchange_calendars.get_calendar = observed
start = Barrier(8)
def registration(_):
    start.wait(timeout=10)
    return next(e for e in schedule.editions(date(2026, 10, 12), 'CQUANT', 'UOWNER',
        kr_close_anchor=(20, 40, 115)) if e.kind == 'pm')
with ThreadPoolExecutor(8) as pool:
    editions = list(pool.map(registration, range(8)))
assert peak == 1, f'Concurrent calendar construction: {peak}'
assert len({e.model_dump_json() for e in editions}) == 1
assert [getattr(editions[0], key).strftime('%H:%M') for key in
    ('starts_at', 'cutoff', 'due_at')] == ['15:50', '16:10', '17:25']
'''
    subprocess.run([sys.executable, '-c', code], check=True, timeout=60)
