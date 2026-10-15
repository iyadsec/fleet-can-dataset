"""SUMO mobility source accessed through TraCI.

SUMO time is the authoritative experiment clock. Time is tracked as an integer number of
nanoseconds (step_index * step_ns) to avoid floating-point drift, and cross-checked against
traci.simulation.getTime() every step.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import traci
import traci.constants as tc

_VARS = (tc.VAR_SPEED, tc.VAR_ACCELERATION, tc.VAR_POSITION, tc.VAR_ANGLE,
         tc.VAR_LANE_ID, tc.VAR_ROAD_ID, tc.VAR_ROUTE_ID)


@dataclass(frozen=True)
class VehicleState:
    """Mobility of one vehicle at one SUMO step (source: SUMO via TraCI)."""
    sim_time_ns: int
    vehicle_id: str
    speed_mps: float
    accel_mps2: float
    x_m: float
    y_m: float
    angle_deg: float        # SUMO heading: degrees clockwise from north
    lane_id: str
    edge_id: str
    route_id: str


def sumo_binary(name: str = "sumo") -> str:
    """Prefer the binary from the eclipse-sumo wheel in the active venv, then PATH."""
    try:
        import sumo  # eclipse-sumo wheel
        cand = os.path.join(sumo.SUMO_HOME, "bin", name)
        if os.path.exists(cand):
            return cand
    except ImportError:
        pass
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"SUMO binary '{name}' not found")
    return found


class SumoController:
    def __init__(self, sumo_config: str, step_length_s: float, seed: int, label: str = "fleet"):
        self.sumo_config = str(sumo_config)
        self.step_ns = round(step_length_s * 1e9)
        self.step_length_s = step_length_s
        self.seed = seed
        self.label = label
        self.step_index = 0
        self.version: str = ""
        self.cmd: list[str] = []
        self._conn = None
        self._subscribed: set[str] = set()

    def start(self) -> None:
        self.cmd = [sumo_binary(), "-c", self.sumo_config,
                    "--step-length", repr(self.step_length_s),
                    "--seed", str(self.seed), "--no-warnings", "false"]
        traci.start(self.cmd, label=self.label)
        self._conn = traci.getConnection(self.label)
        api, ver = self._conn.getVersion()
        self.version = f"{ver} (TraCI API {api})"

    @property
    def sim_time_ns(self) -> int:
        return self.step_index * self.step_ns

    def step(self) -> list[VehicleState]:
        """Advance one step and return the state of every vehicle currently in the network."""
        self._conn.simulationStep()
        self.step_index += 1
        sumo_t = self._conn.simulation.getTime()
        if abs(sumo_t * 1e9 - self.sim_time_ns) > 1e3:   # > 1 us disagreement => clock bug
            raise RuntimeError(f"clock mismatch: SUMO {sumo_t} s vs internal {self.sim_time_ns} ns")

        for vid in self._conn.simulation.getDepartedIDList():
            self._conn.vehicle.subscribe(vid, _VARS)
            self._subscribed.add(vid)
        for vid in self._conn.simulation.getArrivedIDList():
            self._subscribed.discard(vid)

        states = []
        for vid, r in sorted(self._conn.vehicle.getAllSubscriptionResults().items()):
            if vid not in self._subscribed or not r:
                continue
            x, y = r[tc.VAR_POSITION]
            states.append(VehicleState(
                sim_time_ns=self.sim_time_ns, vehicle_id=vid,
                speed_mps=r[tc.VAR_SPEED], accel_mps2=r[tc.VAR_ACCELERATION],
                x_m=x, y_m=y, angle_deg=r[tc.VAR_ANGLE],
                lane_id=r[tc.VAR_LANE_ID], edge_id=r[tc.VAR_ROAD_ID], route_id=r[tc.VAR_ROUTE_ID]))
        return states

    def vehicles_remaining(self) -> int:
        return self._conn.simulation.getMinExpectedNumber()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
