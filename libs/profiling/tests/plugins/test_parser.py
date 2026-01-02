import pytest
from layered_config_tree import LayeredConfigTree
from vivarium.interface.interactive import InteractiveContext
from vivarium_public_health.disease import DiseaseModel
from vivarium_public_health.results import DiseaseObserver
from vivarium_public_health.results.risk import CategoricalRiskObserver
from vivarium_public_health.risks.base_risk import Risk

from tests.conftest import IS_ON_SLURM, TEST_ARTIFACT_PATH
from vivarium_profiling.plugins.parser import (
    MultiComponentParser,
    MultiComponentParsingErrors,
)


def test_multi_component_parser():
    """Test the MultiComponentParser with multiple different causes."""

    # Create test configuration with multiple causes
    config_dict = {
        "causes": {
            "lower_respiratory_infections": {
                "number": 2,
                "duration": "28",
                "observers": True,
            },
            "ischemic_stroke": {
                "number": 3,
                "duration": "14",
                "observers": False,
            },
        }
    }

    config = LayeredConfigTree(config_dict)

    # Create parser and parse configuration
    parser = MultiComponentParser()
    components = parser.parse_component_config(config)

    assert len(components) == 7

    # Check that we have the right component names
    component_names = {component.name for component in components}
    expected_names = {
        "disease_model.lower_respiratory_infections_1",
        "disease_model.lower_respiratory_infections_2",
        "disease_observer.lower_respiratory_infections_1",
        "disease_observer.lower_respiratory_infections_2",
        "disease_model.ischemic_stroke_1",
        "disease_model.ischemic_stroke_2",
        "disease_model.ischemic_stroke_3",
    }

    assert component_names == expected_names

    # Check specific mortality sources for each cause type
    lri_components = [
        c for c in components if "disease_model.lower_respiratory_infections" in c.name
    ]
    lri_observers = [
        c for c in components if "disease_observer.lower_respiratory_infections" in c.name
    ]
    ihd_components = [c for c in components if "ischemic_stroke" in c.name]

    assert len(lri_components) == 2
    assert len(ihd_components) == 3
    assert len(lri_observers) == 2

    for component in lri_components + ihd_components:
        assert isinstance(component, DiseaseModel)

    for observer in lri_observers:
        assert isinstance(observer, DiseaseObserver)
        assert observer.name.startswith("disease_observer.lower_respiratory_infections")


def test_multi_component_parser_risks():
    """Test multi-risk configuration with observers."""

    config_dict = {
        "causes": {
            "lower_respiratory_infections": {
                "number": 2,
                "duration": "28",
                "observers": False,
            }
        },
        "risks": {
            "high_systolic_blood_pressure": {
                "number": 1,
                "observers": False,  # Continuous risk
            },
            "unsafe_water_source": {
                "number": 2,
                "observers": True,
            },
        },
    }

    config = LayeredConfigTree(config_dict)

    parser = MultiComponentParser()
    components = parser.parse_component_config(config)

    # Expected counts:
    # # Causes: 2 disease models
    # high_sbp: 1 Risk; observers skipped
    # unsafe_water: 2 Risk + 2 CategoricalRiskObserver
    assert len(components) == 7

    risks = [c for c in components if isinstance(c, Risk)]
    cat_observers = [c for c in components if isinstance(c, CategoricalRiskObserver)]
    disease_models = [c for c in components if isinstance(c, DiseaseModel)]

    assert len(disease_models) == 2
    assert len(risks) == 3
    assert len(cat_observers) == 2

    # Continuous risk observer skipped
    assert all("high_systolic_blood_pressure" not in obs.risk for obs in cat_observers)

    # Categorical observers created for dichotomous risk
    observer_risks = {obs.risk for obs in cat_observers}
    assert observer_risks == {"unsafe_water_source_1", "unsafe_water_source_2"}


def test_error_when_cause_defined_in_both_multi_config_and_standard():
    """Test validation error when the same cause is defined in both places."""

    # Create a config that defines a cause in both the 'causes' multi-config block
    # and as a standard component
    config_dict = {
        "causes": {
            "lower_respiratory_infections": {
                "number": 2,
                "duration": "28",
                "observers": False,
            }
        },
        "vivarium_public_health": {
            "disease": ["SIS_fixed_duration('lower_respiratory_infections', '28')"]
        },
    }

    config = LayeredConfigTree(config_dict)
    parser = MultiComponentParser()

    with pytest.raises(
        MultiComponentParsingErrors,
        match="Please do not define the same cause in both 'causes' multi-config and as a standard component.",
    ):
        parser.parse_component_config(config)


@pytest.mark.slow
@pytest.mark.skipif(not IS_ON_SLURM, reason="Integration test requires SLURM environment")
def test_multi_component_parser_simulation():
    """Integration test that instantiates a SimulationContext and runs a few timesteps."""

    plugin_configuration = {
        "required": {
            "component_configuration_parser": {
                "controller": "vivarium_profiling.plugins.parser.MultiComponentParser"
            },
            "data": {"controller": "vivarium_profiling.plugins.artifact.ArtifactManager"},
        }
    }

    component_configuration = {
        "causes": {
            "lower_respiratory_infections": {
                "number": 2,
                "duration": "28",
                "observers": True,
            }
        },
        "risks": {
            "unsafe_water_source": {
                "number": 1,
                "observers": True,
            }
        },
        "vivarium_public_health": {
            "population": ["BasePopulation()", "Mortality()"],
            "results": ["ResultsStratifier()", "MortalityObserver()"],
        },
    }

    configuration = {
        "input_data": {
            "input_draw_number": 0,
            "artifact_path": TEST_ARTIFACT_PATH,
        },
        "interpolation": {"order": 0, "extrapolate": True},
        "randomness": {
            "map_size": 1_000_000,
            "key_columns": ["entrance_time", "age"],
            "random_seed": 0,
        },
        "time": {
            "start": {"year": 2022, "month": 1, "day": 1},
            "end": {"year": 2022, "month": 3, "day": 1},
            "step_size": 28,
        },
        "population": {
            "population_size": 100,
            "initialization_age_min": 0,
            "initialization_age_max": 100,
            "untracking_age": 110,
        },
        "stratification": {"default": ["age_group", "sex"]},
    }

    # Instantiate and setup the simulation
    sim = InteractiveContext(
        components=component_configuration,
        configuration=configuration,
        plugin_configuration=plugin_configuration,
    )

    # Verify components were created correctly
    component_names = sim._component_manager.list_components().keys()
    assert "disease_model.lower_respiratory_infections_1" in component_names
    assert "disease_model.lower_respiratory_infections_2" in component_names
    assert "disease_observer.lower_respiratory_infections_1" in component_names
    assert "disease_observer.lower_respiratory_infections_2" in component_names
    assert "risk_factor.unsafe_water_source_1" in component_names
    assert "categorical_risk_observer.unsafe_water_source_1" in component_names

    # Run the simulation for a few timesteps
    sim.take_steps(3)
