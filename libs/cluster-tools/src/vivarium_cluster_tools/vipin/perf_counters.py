"""
==========================
vipin Performance Counters
==========================

Structs for counting performance metrics.

"""
import json
from time import time
from typing import Any, NamedTuple

import psutil


class CounterSnapshot:
    def __init__(
        self,
        cpu: psutil._common.scpustats | None = None,
        disk: psutil._common.sdiskio | None = None,
        freq: psutil._common.scpufreq | None = None,
        net: psutil._common.snetio | None = None,
        timestamp: float | None = None,
    ) -> None:
        self.cpu = psutil.cpu_stats() if cpu is None else cpu
        self.disk = (
            psutil.disk_io_counters(perdisk=False, nowrap=True) if disk is None else disk
        )
        self.freq = psutil.cpu_freq(percpu=False) if freq is None else freq
        self.net = psutil.net_io_counters(pernic=False, nowrap=True) if net is None else net
        self.timestamp = time() if timestamp is None else timestamp

    def to_dict(self) -> dict[str, dict[str, int | float] | float]:
        c_dict: dict[str, dict[str, int | float] | float] = {}
        # Handle potential None values from psutil calls
        if self.cpu is not None:
            c_dict["cpu"] = dict(self.cpu._asdict())
        if self.freq is not None:
            c_dict["freq"] = dict(self.freq._asdict())
        if self.disk is not None:
            c_dict["disk"] = dict(self.disk._asdict())
        if self.net is not None:
            c_dict["net"] = dict(self.net._asdict())
        c_dict["time"] = self.timestamp
        return c_dict

    def __sub__(self, other: "CounterSnapshot") -> "CounterSnapshot":
        # Handle potential None values and use Any for psutil types
        cpu = None
        if self.cpu is not None and other.cpu is not None:
            cpu = type(other.cpu)(
                *tuple(self.cpu[i] - other.cpu[i] for i in range(len(other.cpu)))
            )

        disk = None
        if self.disk is not None and other.disk is not None:
            disk = type(other.disk)(
                *tuple(self.disk[i] - other.disk[i] for i in range(len(other.disk)))
            )

        freq = None
        if self.freq is not None and other.freq is not None:
            freq = type(other.freq)(
                *tuple((self.freq[i] + other.freq[i]) / 2 for i in range(len(other.freq)))
            )

        net = None
        if self.net is not None and other.net is not None:
            net = type(other.net)(
                *tuple(self.net[i] - other.net[i] for i in range(len(other.net)))
            )

        timestamp = self.timestamp - other.timestamp
        return CounterSnapshot(cpu, disk, freq, net, timestamp)

    def __repr__(self) -> str:
        return json.dumps(self.to_dict())
