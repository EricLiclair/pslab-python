"""These tests are intended to run against real hardware.

Before running the tests, connect SQ1<->ID1, SQ2<->ID2, SQ3<->ID3, and SQ4<->ID4.
"""

import os.path
import pickle
import time

import numpy as np
import pytest

import PSL.commands_proto as CP
from PSL import logic_analyzer
from PSL import packet_handler
from PSL import sciencelab

LOGDIR = os.path.join("tests", "recordings", "logic_analyzer")

MAX_SAMPLES = CP.MAX_SAMPLES // 4 - 2
FREQUENCY = 1e5
LOW_FREQUENCY = 100
LOWER_FREQUENCY = 10
DUTY_CYCLE = 0.5
PHASE = 0.25
MICROSECONDS = 1e6
RELTOL = 0.02


def get_frequency(test_name):
    low_frequency_tests = (
        "test_capture_four_low_frequency",
        "test_capture_four_lower_frequency",
        "test_capture_four_lowest_frequency",
        "test_capture_timeout",
        "test_get_states",
    )
    if test_name in low_frequency_tests:
        return LOW_FREQUENCY
    elif test_name == "test_capture_four_too_low_frequency":
        return LOWER_FREQUENCY
    else:
        return FREQUENCY


@pytest.fixture
def scaffold(monkeypatch, request, integration, record):
    if record:
        integration = True

    test_name = request.node.name
    handler = get_handler(monkeypatch, test_name, integration)

    if record:
        handler._logging = True

    yield logic_analyzer.LogicAnalyzer(handler)

    if record:
        log = handler._log.split(b"STOP")[:-1]
        record_traffic(test_name, log)


def get_handler(monkeypatch, test_name: str, integration: bool = True):
    if integration:
        psl = sciencelab.connect()
        psl.sqrPWM(
            freq=get_frequency(test_name),
            h0=DUTY_CYCLE,
            p1=PHASE,
            h1=DUTY_CYCLE,
            p2=2 * PHASE,
            h2=DUTY_CYCLE,
            p3=3 * PHASE,
            h3=DUTY_CYCLE,
        )
        return psl.H
    else:
        logfile = os.path.join(LOGDIR, test_name + ".pkl")
        recorded_traffic = pickle.load(open(logfile, "rb"))
        monkeypatch.setattr(packet_handler, "RECORDED_TRAFFIC", recorded_traffic)
        return packet_handler.MockHandler()


def record_traffic(test_name: str, log: list):
    tx = []
    rx = []

    for b in log:
        direction = b[:2]
        data = b[2:]
        if direction == b"TX":
            tx.append(data)
            rx.append(b"")
        elif direction == b"RX":
            rx[-1] += data
        else:
            raise ValueError("Unknown direction: {direction}")

    logfile = os.path.join(LOGDIR, test_name + ".pkl")
    pickle.dump(zip(tx, rx), open(logfile, "wb"))


def test_capture_one_channel(scaffold):
    t = scaffold.capture(1)
    assert len(t[0]) == MAX_SAMPLES


def test_capture_two_channels(scaffold):
    t1, t2 = scaffold.capture(2)
    assert len(t1) == len(t2) == MAX_SAMPLES


def test_capture_four_channels(scaffold):
    t1, t2, t3, t4 = scaffold.capture(4)
    assert len(t1) == len(t2) == len(t3) == len(t4) == MAX_SAMPLES


def test_capture_four_low_frequency(scaffold):
    e2e_time = (LOW_FREQUENCY ** -1) / 2
    t1 = scaffold.capture(4, 10, e2e_time=e2e_time)[0]
    assert np.array(9 * [e2e_time * MICROSECONDS]) == pytest.approx(
        np.diff(t1), rel=RELTOL
    )


def test_capture_four_lower_frequency(scaffold):
    e2e_time = LOW_FREQUENCY ** -1
    t1 = scaffold.capture(4, 10, modes=4 * ["rising"], e2e_time=e2e_time)[0]
    assert np.array(9 * [e2e_time * MICROSECONDS]) == pytest.approx(
        np.diff(t1), rel=RELTOL
    )


def test_capture_four_lowest_frequency(scaffold):
    e2e_time = (LOW_FREQUENCY ** -1) * 16
    t1 = scaffold.capture(
        4, 10, modes=4 * ["sixteen rising"], e2e_time=e2e_time, timeout=2
    )[0]
    assert np.array(9 * [e2e_time * MICROSECONDS]) == pytest.approx(
        np.diff(t1), rel=RELTOL
    )


def test_capture_four_too_low_frequency(scaffold):
    e2e_time = (LOWER_FREQUENCY ** -1) * 4
    with pytest.raises(ValueError):
        scaffold.capture(4, 10, modes=4 * ["four rising"], e2e_time=e2e_time, timeout=5)


def test_capture_nonblocking(scaffold):
    scaffold.capture(1, block=False)
    time.sleep(MAX_SAMPLES * FREQUENCY ** -1)
    t = scaffold.fetch_data()
    assert len(t[0]) == MAX_SAMPLES


def test_capture_rising_edges(scaffold):
    t1, t2 = scaffold.capture(2, 100, modes=["any", "rising"])
    t1 -= t1[0]
    t2 -= t2[0]
    assert t2[-1] == pytest.approx(2 * t1[-1], rel=RELTOL)


def test_capture_four_rising_edges(scaffold):
    t1, t2 = scaffold.capture(2, 100, modes=["rising", "four rising"])
    t1 -= t1[0]
    t2 -= t2[0]
    assert t2[-1] == pytest.approx(4 * t1[-1], rel=RELTOL)


def test_capture_sixteen_rising_edges(scaffold):
    t1, t2 = scaffold.capture(2, 100, modes=["four rising", "sixteen rising"])
    t1 -= t1[0]
    t2 -= t2[0]
    assert t2[-1] == pytest.approx(4 * t1[-1], rel=RELTOL)


def test_capture_too_many_events(scaffold):
    with pytest.raises(ValueError):
        scaffold.capture(1, MAX_SAMPLES + 1)


def test_capture_too_many_channels(scaffold):
    with pytest.raises(ValueError):
        scaffold.capture(5)


def test_capture_timeout(scaffold):
    events = 100
    timeout = (events * LOW_FREQUENCY ** -1) / 4
    with pytest.raises(RuntimeError):
        scaffold.capture(1, timeout=timeout)


def test_measure_frequency(scaffold):
    frequency = scaffold.measure_frequency("ID1", timeout=0.1)
    assert FREQUENCY == pytest.approx(frequency, rel=RELTOL)


def test_measure_frequency_firmware(scaffold):
    frequency = scaffold.measure_frequency(
        "ID2", timeout=0.1, simultaneous_oscilloscope=True
    )
    assert FREQUENCY == pytest.approx(frequency, rel=RELTOL)


def test_measure_interval(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID1", "ID2"], modes=["rising", "falling"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * -PHASE * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_interval_same_channel(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID1", "ID1"], modes=["rising", "falling"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * DUTY_CYCLE * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_interval_same_channel_any(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID1", "ID1"], modes=["any", "any"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * DUTY_CYCLE * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_interval_same_channel_four_rising(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID1", "ID1"], modes=["rising", "four rising"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * 3 * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_interval_same_channel_sixteen_rising(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID1", "ID1"], modes=["rising", "sixteen rising"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * 15 * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_interval_same_channel_same_event(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    interval = scaffold.measure_interval(
        channels=["ID3", "ID3"], modes=["rising", "rising"], timeout=0.1
    )
    expected_interval = FREQUENCY ** -1 * MICROSECONDS
    assert expected_interval == pytest.approx(interval, rel=RELTOL)


def test_measure_duty_cycle(scaffold):
    period, duty_cycle = scaffold.measure_duty_cycle("ID4", timeout=0.1)
    expected_period = FREQUENCY ** -1 * MICROSECONDS
    assert (expected_period, DUTY_CYCLE) == pytest.approx(
        (period, duty_cycle), rel=RELTOL
    )


def test_get_xy_rising_trigger(scaffold):
    scaffold.configure_trigger("ID1", "rising")
    t = scaffold.capture(1, 100)
    _, y = scaffold.get_xy(t)
    assert y[0]


def test_get_xy_falling_trigger(scaffold):
    scaffold.configure_trigger("ID1", "falling")
    t = scaffold.capture(1, 100)
    _, y = scaffold.get_xy(t)
    assert not y[0]


def test_get_xy_rising_capture(scaffold):
    t = scaffold.capture(1, 100, modes=["rising"])
    _, y = scaffold.get_xy(t)
    assert sum(y) == 100


def test_get_xy_falling_capture(scaffold):
    t = scaffold.capture(1, 100, modes=["falling"])
    _, y = scaffold.get_xy(t)
    assert sum(~y) == 100


def test_stop(scaffold):
    scaffold.capture(1, modes=["sixteen rising"], block=False)
    time.sleep(MAX_SAMPLES * FREQUENCY ** -1)
    progress_time = time.time()
    progress = scaffold.get_progress()
    scaffold.stop()
    stop_time = time.time()
    time.sleep(MAX_SAMPLES * FREQUENCY ** -1)
    assert progress < 2500
    abstol = FREQUENCY * (stop_time - progress_time)
    assert progress == pytest.approx(scaffold.get_progress(), abs=abstol)


def test_get_states(scaffold):
    time.sleep(LOW_FREQUENCY ** -1)
    states = scaffold.get_states()
    expected_states = {"ID1": True, "ID2": False, "ID3": False, "ID4": True}
    assert states == expected_states


def test_count_pulses(scaffold):
    interval = 0.2
    pulses = scaffold.count_pulses("ID2", interval)
    expected_pulses = FREQUENCY * interval
    assert expected_pulses == pytest.approx(pulses, rel=0.1)  # Pretty bad accuracy.