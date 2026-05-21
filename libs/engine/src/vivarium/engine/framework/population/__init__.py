"""
================================
The Population Management System
================================

This subpackage provides tools for managing the :term:`population state table <Population State Table>`
in a :mod:`vivarium` simulation, which is the record of all simulants in a
simulation and their state. Its main tasks are managing the creation of new
simulants and providing the ability for components to view and update simulant
state safely during runtime.

"""

from vivarium.engine.framework.population.exceptions import PopulationError
from vivarium.engine.framework.population.interface import PopulationInterface
from vivarium.engine.framework.population.manager import PopulationManager, SimulantData
from vivarium.engine.framework.population.population_view import PopulationView
