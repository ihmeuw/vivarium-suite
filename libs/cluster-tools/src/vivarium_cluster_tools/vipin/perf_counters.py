"""
==========================
vipin Performance Counters
==========================

Structs for counting performance metrics.

"""
import json
from time import time

import psutil


class CounterSnapshot:
    def __init__(self, cpu=None, disk=None, freq=None, net=None, timestamp=None):
        self.cpu = psutil.cpu_stats() if cpu is None else cpu
        self.disk = (
            psutil.disk_io_counters(perdisk=False, nowrap=True) if disk is None else disk
        )
        self.freq = psutil.cpu_freq(percpu=False) if freq is None else freq
        self.net = psutil.net_io_counters(pernic=False, nowrap=True) if net is None else net
        self.timestamp = time() if timestamp is None else timestamp

    def to_dict(self):
        c_dict = dict()
        c_dict["cpu"] = dict(self.cpu._asdict())
        c_dict["freq"] = dict(self.freq._asdict())
        c_dict["disk"] = dict(self.disk._asdict())
        c_dict["net"] = dict(self.net._asdict())
        c_dict["time"] = self.timestamp
        return c_dict

    def __sub__(self, other):
        cpu = type(other.cpu)(
            *tuple(self.cpu[i] - other.cpu[i] for i in range(len(other.cpu)))
        )
        disk = type(other.disk)(
            *tuple(self.disk[i] - other.disk[i] for i in range(len(other.disk)))
        )
        freq = type(other.freq)(
            *tuple((self.freq[i] + other.freq[i]) / 2 for i in range(len(other.freq)))
        )
        net = type(other.net)(
            *tuple(self.net[i] - other.net[i] for i in range(len(other.net)))
        )
        timestamp = self.timestamp - other.timestamp
        return CounterSnapshot(cpu, disk, freq, net, timestamp)

    def __repr__(self) -> str:
        return json.dumps(self.to_dict())
