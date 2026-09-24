"""Measured energy per request, from the Windows Energy Meter Interface (EMI).

`provenance.energy_proxy()` exists because a WSL2 client sees no power rail.
A harness running on the Windows host does: Snapdragon X systems expose EMI
rails as the "Energy Meter" performance counters — cumulative energy in
picowatt-hours per rail, stamped in milliseconds since 1601. Measured on a
Snapdragon X X126100 (2026-09-24): `CPU_CLUSTER_0` and `CPU_CLUSTER_1` carry
data, while `SYS`, `PSU_USB` and `USBC_TOTAL` read zero. The Hexagon NPU and
the Adreno GPU have no rail there, so this measures the CPU clusters, which is
what separates the lanes: the CPU lane pins 7.5 of 8 cores and the NPU lane
orchestrates from 1.65. It says nothing about the NPU's own draw, and every
field is named for that scope.

The meter publishes about once per second, so reading it before and after a
multi-second request would misattribute up to a second at each end. A
background thread records every published sample instead, and
`energy_between()` interpolates the cumulative curve at the exact wall-clock
bounds of the request.

Counters are opened by their ENGLISH names (`PdhAddEnglishCounterW`), so a
localized Windows ("Energiemessung" on a German install) reads the same way.
Anywhere else, or if the counters are absent, `EnergyMeter.available` is
False and callers record null rather than inventing joules.
"""

from __future__ import annotations

import sys
import threading
import time


PWH_TO_J = 3.6e-9  # one picowatt-hour in joules
EPOCH_1601_MS = 11_644_473_600_000  # 1601-01-01 -> 1970-01-01, in ms
ENERGY_COUNTER = "\\Energy Meter(*)\\Energy"
TIME_COUNTER = "\\Energy Meter(*)\\Time"
SCOPE = (
    "EMI CPU-cluster rails only. The NPU and GPU have no rail on this host, "
    "so an NPU lane's own draw is NOT included."
)


class _Pdh:
    """The few PDH calls needed, via ctypes. Windows only."""

    PDH_FMT_LARGE = 0x00000400
    PDH_MORE_DATA = 0x800007D2

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        self._dll = ctypes.WinDLL("pdh.dll")

        class _Value(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [
                    ("longValue", ctypes.c_long),
                    ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong),
                ]

            _anonymous_ = ("u",)
            _fields_ = [("CStatus", wintypes.DWORD), ("u", _U)]

        class _Item(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]

        self._Item = _Item

    def open(self, *paths):
        """One query holding every counter, so one collection reads them together.

        Energy and its timestamp used to be two queries collected one after the
        other; the meter could publish in between, pairing a new stamp with the
        previous second's energy (reproduced twice in 41 samples at 2 ms polls).
        """
        ct, wt = self._ct, self._wt
        query = wt.HANDLE()
        if self._dll.PdhOpenQueryW(None, 0, ct.byref(query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        counters = []
        for path in paths:
            counter = wt.HANDLE()
            rc = self._dll.PdhAddEnglishCounterW(query, path, 0, ct.byref(counter))
            if rc != 0:
                self._dll.PdhCloseQuery(query)
                raise OSError(f"{path} is not available (PDH 0x{rc & 0xFFFFFFFF:08X})")
            counters.append(counter)
        return query, counters

    def read(self, query, counters):
        """[{instance: value}, ...], one dict per counter, from ONE collection."""
        if self._dll.PdhCollectQueryData(query) != 0:
            raise OSError("PdhCollectQueryData failed")
        return [self._values(counter) for counter in counters]

    def _values(self, counter):
        ct, wt = self._ct, self._wt
        size, count = wt.DWORD(0), wt.DWORD(0)
        rc = self._dll.PdhGetFormattedCounterArrayW(
            counter, self.PDH_FMT_LARGE, ct.byref(size), ct.byref(count), None
        )
        if (rc & 0xFFFFFFFF) != self.PDH_MORE_DATA:
            raise OSError(
                f"PdhGetFormattedCounterArrayW sizing: 0x{rc & 0xFFFFFFFF:08X}"
            )
        buf = (ct.c_byte * size.value)()
        rc = self._dll.PdhGetFormattedCounterArrayW(
            counter, self.PDH_FMT_LARGE, ct.byref(size), ct.byref(count), buf
        )
        if rc != 0:
            raise OSError(f"PdhGetFormattedCounterArrayW: 0x{rc & 0xFFFFFFFF:08X}")
        items = ct.cast(buf, ct.POINTER(self._Item))
        return {
            items[i].szName: items[i].FmtValue.largeValue for i in range(count.value)
        }

    def close(self, query):
        self._dll.PdhCloseQuery(query)


def _interpolate(samples, rail, t):
    """Cumulative pWh of `rail` at unix time `t`, linear between samples."""
    before = after = None
    for ts, values in samples:
        if ts <= t:
            before = (ts, values[rail])
        elif after is None:
            after = (ts, values[rail])
            break
    if before is None or after is None:
        return None
    (t0, e0), (t1, e1) = before, after
    return e0 + (e1 - e0) * ((t - t0) / (t1 - t0)) if t1 > t0 else e0


class EnergyMeter:
    """Background sampler over the EMI rails.

    Use as a context manager around a run, then ask `energy_between(t0, t1)`
    for any window inside it, with t0/t1 from `time.time()`.
    """

    def __init__(self, poll_s=0.25, enabled=True):
        self.poll_s = poll_s
        self.available = False
        self.reason = None
        self.rails = []
        self._samples = []  # (unix_s, {rail: pWh}), one per PUBLISHED update
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._pdh = None
        self._query = None
        self.baselines = []  # every idle_power() result, in order
        self.resets = 0  # counter resets seen; a request spanning one is unmetered
        if not enabled:
            self.reason = "disabled (--no-energy)"
            return
        if sys.platform != "win32":
            self.reason = "not Windows: no Energy Meter counters to read"
            return
        try:
            self._pdh = _Pdh()
            self._query = self._pdh.open(ENERGY_COUNTER, TIME_COUNTER)
            energy, stamp = self._read()
        except Exception as e:  # counters absent or PDH refused
            self.reason = f"Energy Meter unavailable: {e}"
            return
        # A rail that has never accumulated anything is not metered here (SYS,
        # PSU_USB and USBC_TOTAL read 0 on the X126100). _Total is the sum.
        self.rails = sorted(r for r, v in energy.items() if v and r != "_Total")
        if not self.rails:
            self.reason = "Energy Meter present but no rail carries data"
            return
        self.available = True
        self._append(energy, stamp)

    def _read(self):
        return self._pdh.read(*self._query)

    def _append(self, energy, stamp):
        ms = max(stamp.get(r, 0) for r in self.rails)
        unix_s = (ms - EPOCH_1601_MS) / 1000.0
        values = {r: energy.get(r, 0) for r in self.rails}
        with self._lock:
            if self._samples:
                last_s, last = self._samples[-1]
                if unix_s <= last_s:
                    return  # the meter has not published since the last poll
                # Rails that all stood still under a new stamp are a torn read.
                if all(values[r] == last[r] for r in self.rails):
                    return
                # A cumulative counter that went backwards was reset: nothing
                # spans the reset, so start the curve again from here rather
                # than refuse every later sample (and lose every later request).
                if any(values[r] < last[r] for r in self.rails):
                    self._samples.clear()
                    self.resets += 1
            self._samples.append((unix_s, values))

    def _run(self):
        while not self._stop.wait(self.poll_s):
            try:
                self._append(*self._read())
            except Exception:  # a missed poll only widens the interpolation
                continue

    def start(self):
        if self.available and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="emi", daemon=True)
            self._thread.start()
        return self

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
        if self._query:
            self._pdh.close(self._query[0])
            self._query = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    def wait_until(self, t, timeout=3.0):
        """Block until the meter has published a sample at or after `t`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._samples and self._samples[-1][0] >= t:
                    return True
            time.sleep(self.poll_s)
        return False

    def energy_between(self, t0, t1):
        """Joules per rail over [t0, t1] (unix seconds), or None if not covered.

        Waits briefly for the meter to publish past `t1`: the sample covering
        the end of a request is published up to a second after it.
        """
        if not self.available or t1 <= t0:
            return None
        self.wait_until(t1)
        with self._lock:
            samples = list(self._samples)
        joules = {}
        for rail in self.rails:
            e0, e1 = _interpolate(samples, rail, t0), _interpolate(samples, rail, t1)
            if e0 is None or e1 is None:
                return None
            joules[rail] = round((e1 - e0) * PWH_TO_J, 3)
        return joules

    def idle_power(self, seconds=5.0):
        """Mean CPU-rail watts over the next `seconds` — the baseline to net out.

        Every result is also kept in `baselines`, so a run can take one before
        and one after its requests and report how far the machine drifted.
        """
        if not self.available or seconds <= 0:
            return None
        t0 = time.time()
        time.sleep(seconds)
        joules = self.energy_between(t0, time.time())
        if joules is None:
            return None
        watts = round(sum(joules.values()) / seconds, 3)
        self.baselines.append(watts)
        return watts


# Two baselines further apart than this make the run's net figures doubtful:
# 0.2 W over a 60 s run is 12 J, a sixth of the NPU lane's whole net energy.
DRIFT_LIMIT_W = 0.2


def energy_block(meter):
    """What a report says about the meter: present, why not, which rails.

    With the idle baselines its net figures were taken against, and their drift.
    """
    block = {
        "available": meter.available,
        "reason": meter.reason,
        "rails": meter.rails,
        "scope": SCOPE,
    }
    if meter.baselines:
        drift = round(max(meter.baselines) - min(meter.baselines), 3)
        block["idle_w"] = meter.baselines
        block["idle_w_used"] = round(sum(meter.baselines) / len(meter.baselines), 3)
        block["idle_drift_w"] = drift
        # One baseline cannot show drift: unknown, not reliable.
        block["net_reliable"] = (
            drift <= DRIFT_LIMIT_W if len(meter.baselines) > 1 else None
        )
    if getattr(meter, "resets", 0):
        block["counter_resets"] = meter.resets
    return block


def energy_lines(meter):
    """The idle-baseline line under the speed table: what net was taken against."""
    block = energy_block(meter) if meter is not None else {}
    if "idle_w" not in block:
        return []
    watts = " then ".join(f"{w:.2f}" for w in block["idle_w"])
    line = f"    Idle baseline:  {watts} W (net uses {block['idle_w_used']:.2f})"
    if block["net_reliable"] is False:
        line += (
            f"  -- DRIFTED {block['idle_drift_w']:.2f} W: the net figures are not "
            "reliable, read gross"
        )
    return [line]


def renet(results, meter):
    """Net every metered row against the MEAN of the run's idle baselines.

    Rows are netted while the run goes, against the baseline taken before it.
    Measured on 2026-09-24: that single 5-s window read 1.26 W in one NPU run
    and 1.84 W in the next, and the lower one turned a +21 % gross difference
    into a published "+70 % net". Once the after-run baseline exists the rows
    are netted again, so a row and the summary agree. Returns the watts used.
    """
    if meter is None or not meter.baselines:
        return None
    idle_w = round(sum(meter.baselines) / len(meter.baselines), 3)
    for row in results:
        gross, window = row.get("cpu_rail_energy_j"), row.get("cpu_rail_window_s")
        if gross is None or not window:
            continue
        net = max(0.0, gross - idle_w * window)
        row["cpu_rail_idle_w"] = idle_w
        row["cpu_rail_net_energy_j"] = round(net, 3)
        if row.get("completion_tokens"):
            row["cpu_rail_net_j_per_token"] = round(net / row["completion_tokens"], 4)
    return idle_w


def request_energy(meter, t0, t1, completion_tokens, idle_w=None):
    """The per-request energy fields a result row carries, or {} when unmetered."""
    if meter is None or not meter.available:
        return {}
    joules = meter.energy_between(t0, t1)
    if joules is None:
        return {}
    total = round(sum(joules.values()), 3)
    fields = {
        "cpu_rail_energy_j": total,
        "cpu_rail_window_s": round(t1 - t0, 3),
        "cpu_rail_avg_w": round(total / (t1 - t0), 3),
        "cpu_rail_energy_by_rail_j": joules,
    }
    if completion_tokens:
        fields["cpu_rail_j_per_token"] = round(total / completion_tokens, 4)
    if idle_w is not None:
        net = max(0.0, total - idle_w * (t1 - t0))
        fields["cpu_rail_net_energy_j"] = round(net, 3)
        if completion_tokens:
            fields["cpu_rail_net_j_per_token"] = round(net / completion_tokens, 4)
    return fields
