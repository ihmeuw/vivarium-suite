from layered_config_tree import LayeredConfigTree
from vivarium import Component
from vivarium.framework.components import ComponentConfigurationParser
from vivarium.framework.components.parser import ParsingError
from vivarium_public_health.disease import DiseaseModel
from vivarium_public_health.disease.models import SIS_fixed_duration
from vivarium_public_health.results import DiseaseObserver

CAUSE_KEY = "causes"
DEFAULT_SIS_CONFIG = {"duration": 1, "number": 1, "observers": False}


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

        if CAUSE_KEY in component_config:
            causes_config = component_config[CAUSE_KEY]
            self._validate_causes_config(causes_config)
            components += self._get_multi_disease_components(causes_config)

        # Parse standard components (i.e. not multi components)
        standard_component_config = component_config.to_dict()
        standard_component_config.pop(CAUSE_KEY, None)
        standard_components = (
            self.process_level(standard_component_config, [])
            if standard_component_config
            else []
        )

        return components + standard_components

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

    def _validate_causes_config(self, causes_config: LayeredConfigTree) -> None:
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
