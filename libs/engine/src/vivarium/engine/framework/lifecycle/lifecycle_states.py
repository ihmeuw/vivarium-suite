"""
================
Lifecycle States
================

This module defines the states and phases of the simulation lifecycle.

"""

from dataclasses import dataclass

# Initialization states
INITIALIZATION = "initialization"
"""The name of the initialization state in the simulation lifecycle."""

# Setup states
SETUP = "setup"
"""The name of the setup state in the simulation lifecycle."""

POST_SETUP = "post_setup"
"""The name of the post-setup state in the simulation lifecycle."""

POPULATION_CREATION = "population_creation"
"""The name of the population creation state in the simulation lifecycle."""

#  Main loop states
TIME_STEP_PREPARE = "time_step__prepare"
"""The name of the time step preparation state in the simulation lifecycle."""
TIME_STEP = "time_step"
"""The name of the time step state in the simulation lifecycle."""
TIME_STEP_CLEANUP = "time_step__cleanup"
"""The name of the time step cleanup state in the simulation lifecycle."""
COLLECT_METRICS = "collect_metrics"
"""The name of the collect metrics state in the simulation lifecycle."""

# Simulation end states
SIMULATION_END = "simulation_end"
"""The name of the simulation end state in the simulation lifecycle."""
REPORT = "report"
"""The name of the report state in the simulation lifecycle."""


@dataclass(frozen=True)
class Phase:
    """A phase of the simulation lifecycle."""

    name: str
    """The name of the phase."""
    states: tuple[str, ...]
    """The names of the states in the phase, in their order of execution."""
    loop: bool = False
    """Whether the states in the phase repeat."""


PHASES = (
    Phase("setup", (SETUP, POST_SETUP, POPULATION_CREATION)),
    Phase(
        "main_loop",
        (TIME_STEP_PREPARE, TIME_STEP, TIME_STEP_CLEANUP, COLLECT_METRICS),
        loop=True,
    ),
    Phase("simulation_end", (SIMULATION_END, REPORT)),
)
"""The phases the simulation adds to the lifecycle, in their order of execution.

The initialization phase is not included, because the lifecycle creates it itself.
"""

ALL_STATES = (INITIALIZATION, *(state for phase in PHASES for state in phase.states))
"""The names of all states in the simulation lifecycle, in their order of execution."""
