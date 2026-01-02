from layered_config_tree import LayeredConfigTree
from vivarium import Component
from vivarium.framework.components import ComponentConfigurationParser
from vivarium.framework.components.parser import ParsingError
from vivarium_public_health.disease import DiseaseModel
from vivarium_public_health.disease.models import SIS_fixed_duration
from vivarium_public_health.results import DiseaseObserver
from vivarium_public_health.results.risk import CategoricalRiskObserver
from vivarium_public_health.risks.base_risk import Risk

CAUSE_KEY = "causes"
RISK_KEY = "risks"
DEFAULT_SIS_CONFIG = {"duration": 1, "number": 1, "observers": False}
DEFAULT_RISK_CONFIG = {
    "number": 1,
    "observers": False,
}


class MultiComponentParsingErrors(ParsingError):
    """Error raised when there are any errors parsing a multi-configuration."""

    def __init__(self, messages: list[str]):
        super().__init__("\n - " + "\n - ".join(messages))


class MultiComponentParser(ComponentConfigurationParser):
    """Parser for multi-component configurations.

    Component configuration parser that can automatically generate multiple
    instances of components based on a multi-configuration. Currently implements
    disease models as SIS_fixed_duration and optionally creates disease observers.

    Example configuration:

    .. code-block:: yaml

        components:
            causes:
                lower_respiratory_infections:
                    number: 5
                    duration: 28
                    observers: True
                ischemic_heart_disease:
                    number: 3
                    duration: 14
                    observers: False

    This will create disease components named:
    - lower_respiratory_infections_1, lower_respiratory_infections_2, ..., lower_respiratory_infections_5
    - ischemic_heart_disease_1, ischemic_heart_disease_2, ischemic_heart_disease_3

    And if observers: True, it will also create corresponding DiseaseObserver components.
    """

    def parse_component_config(self, component_config: LayeredConfigTree) -> list[Component]:
        """Parses the component configuration and returns a list of components.

        This method looks for a `causes` key that contains multi-configuration
        for disease components where each cause name is a key with its own
        multi-parameters. We then separately parse standard (i.e. non-multi-) components.

        Parameters
        ----------
        component_config
            A LayeredConfigTree defining the components to initialize.

        Returns
        -------
            A list of initialized components.

        Raises
        ------
        MultiComponentParsingErrors
            If the multi-configuration is invalid
        """
        components = []

        # Parse standard components first so we can extract disease models for validation
        standard_component_config = component_config.to_dict()
        standard_component_config.pop(CAUSE_KEY, None)
        standard_component_config.pop(RISK_KEY, None)
        standard_components = (
            self.process_level(standard_component_config, [])
            if standard_component_config
            else []
        )

        # Extract normally-defined disease causes
        standard_causes = {
            cause.name.split(".")[-1]
            for cause in standard_components
            if isinstance(cause, DiseaseModel)
        }

        if CAUSE_KEY in component_config:
            causes_config = component_config[CAUSE_KEY]
            self._validate_causes_config(causes_config, standard_causes)
            components += self._get_multi_disease_components(causes_config)

        if RISK_KEY in component_config:
            risks_config = component_config[RISK_KEY]
            self._validate_risks_config(risks_config)
            components += self._get_multi_risk_components(risks_config)

        # Add standard components last so that we don't setup results before diseases
        components += standard_components

        return components

    def _get_multi_disease_components(
        self, causes_config: LayeredConfigTree
    ) -> list[Component]:
        """Creates multiple disease components based on multi-configuration.

        Parameters
        ----------
        causes_config
            A LayeredConfigTree defining the disease multi-configuration
            where each cause name is a key with multi-parameters

        Returns
        -------
            A list of initialized disease components and optionally disease observers
        """
        components = []

        # Iterate over each cause in the configuration
        for cause_name, cause_config in causes_config.items():

            number = cause_config.get("number", DEFAULT_SIS_CONFIG["number"])
            duration = cause_config.get("duration", DEFAULT_SIS_CONFIG["duration"])
            observers = cause_config.get("observers", DEFAULT_SIS_CONFIG["observers"])

            for i in range(number):
                components.append(SIS_fixed_duration(f"{cause_name}_{i + 1}", duration))

                if observers:
                    components.append(DiseaseObserver(f"{cause_name}_{i + 1}"))

        return components

    def _validate_causes_config(
        self, causes_config: LayeredConfigTree, standard_causes: set[str]
    ) -> None:
        """Validates the diseases multi-configuration.

        Parameters
        ----------
        causes_config
            A LayeredConfigTree defining the diseases multi-configuration
            where each cause name is a key with multi-parameters

        Raises
        ------
        MultiComponentParsingErrors
            If the diseases multi-configuration is invalid
        """
        error_messages = []

        # Validate each cause configuration
        for cause_name, cause_config in causes_config.items():
            if cause_name in standard_causes:
                error_messages.extend(
                    [
                        "Please do not define the same cause in both 'causes' multi-config and as a standard component."
                    ]
                )

            cause_errors = self._validate_cause_config(cause_name, cause_config)
            if cause_errors:
                error_messages.extend(
                    [
                        f"Error in cause '{cause_name}': {str(cause_error)}"
                        for cause_error in cause_errors
                    ]
                )
        if error_messages:
            raise MultiComponentParsingErrors(error_messages)

    def _validate_cause_config(
        self, cause_name: str, cause_config: LayeredConfigTree
    ) -> None:
        """Validates the configuration for a single cause.

        Parameters
        ----------
        cause_name
            The name of the cause
        cause_config
            A LayeredConfigTree defining the cause multi-configuration

        Raises
        ------
        MultiComponentParsingErrors
            If the cause multi-configuration is invalid
        """
        cause_config_dict = cause_config.to_dict()
        error_messages = []

        # Validate number
        if "number" in cause_config_dict:
            try:
                number = int(cause_config_dict["number"])
                if number <= 0:
                    error_messages.append("Number of components must be positive")
            except (ValueError, TypeError):
                error_messages.append("Number of components must be a valid integer")

        # Validate duration if provided
        if "duration" in cause_config_dict:
            try:
                duration = float(cause_config_dict["duration"])
                if duration <= 0.0:
                    error_messages.append("Duration must be positive")
            except (ValueError, TypeError):
                error_messages.append("Duration must be a valid number")

        # Validate observers if provided
        if "observers" in cause_config_dict:
            observers = cause_config_dict["observers"]
            if not isinstance(observers, bool):
                error_messages.append("Observers must be a boolean value (True or False)")

        return error_messages

    def _get_multi_risk_components(
        self,
        risks_config: LayeredConfigTree,
    ) -> list[Component]:
        components: list[Component] = []

        for risk_name, risk_config in risks_config.items():
            number = int(risk_config.get("number", DEFAULT_RISK_CONFIG["number"]))
            observers = risk_config.get("observers", DEFAULT_RISK_CONFIG["observers"])

            for i in range(number):
                suffixed_risk_name = f"{risk_name}_{i + 1}"
                components.append(Risk(f"risk_factor.{suffixed_risk_name}"))

                if observers:
                    # Unfortunately, it is problematic to try to determine in advance
                    # whether a risk is continuous or categorical without instantiating
                    # the Risk component first. So we always try to add a CategoricalRiskObserver.
                    # Users must remember to not add observers for continuous risks.
                    components.append(CategoricalRiskObserver(suffixed_risk_name))

        return components

    def _validate_risks_config(
        self,
        risks_config: LayeredConfigTree,
    ) -> None:
        error_messages = []

        for risk_name, risk_config in risks_config.items():
            risk_errors = self._validate_risk_config(risk_name, risk_config)
            if risk_errors:
                error_messages.extend(
                    [
                        f"Error in risk '{risk_name}': {str(risk_error)}"
                        for risk_error in risk_errors
                    ]
                )

        if error_messages:
            raise MultiComponentParsingErrors(error_messages)

    def _validate_risk_config(
        self,
        risk_name: str,
        risk_config: LayeredConfigTree,
    ) -> list[str]:
        risk_config_dict = risk_config.to_dict()
        error_messages = []

        if "number" in risk_config_dict:
            try:
                number = int(risk_config_dict["number"])
                if number <= 0:
                    error_messages.append("Number of components must be positive")
            except (ValueError, TypeError):
                error_messages.append("Number of components must be a valid integer")

        if "observers" in risk_config_dict:
            observers = risk_config_dict["observers"]
            if not isinstance(observers, bool):
                error_messages.append("Observers must be a boolean value (True or False)")

        return error_messages
