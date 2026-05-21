from pathlib import Path

from vivarium.engine import InteractiveContext
from vivarium.engine.examples.disease_model.disease import (
    DiseaseModel,
    DiseaseState,
    DiseaseTransition,
    SISDiseaseModel,
)
from vivarium.engine.examples.disease_model.intervention import TreatmentIntervention
from vivarium.engine.examples.disease_model.mortality import Mortality
from vivarium.engine.examples.disease_model.observer import DeathsObserver, YllsObserver
from vivarium.engine.examples.disease_model.population import BasePopulation
from vivarium.engine.examples.disease_model.risk import Risk, RiskEffect


def get_model_specification_path() -> str:
    p = Path(__file__).parent / "disease_model.yaml"
    return str(p)


def get_disease_model_simulation() -> InteractiveContext:
    p = get_model_specification_path()
    return InteractiveContext(p)
