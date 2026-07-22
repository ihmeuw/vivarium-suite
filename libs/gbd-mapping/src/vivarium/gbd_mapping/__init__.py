"""vivarium.gbd_mapping

A programmatically accessible mapping of GBD entities.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-gbd-mapping")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.gbd_mapping.base_template import (
    Categories,
    GbdRecord,
    ModelableEntity,
    Restrictions,
    Tmred,
)
from vivarium.gbd_mapping.cause import Cause, causes
from vivarium.gbd_mapping.covariate import Covariate, covariates
from vivarium.gbd_mapping.etiology import Etiology, etiologies
from vivarium.gbd_mapping.id import (
    UNKNOWN,
    UnknownEntityError,
    c_id,
    cov_id,
    hs_id,
    me_id,
    rei_id,
    s_id,
    scalar,
)
from vivarium.gbd_mapping.risk_factor import RiskFactor, risk_factors
from vivarium.gbd_mapping.sequela import Healthstate, Sequela, sequelae
