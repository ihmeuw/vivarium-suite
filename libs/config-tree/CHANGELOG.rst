**4.0.1 - 07/16/2025**

 - Support pinning of vivarium_build_utils; pin vivarium_build_utils>=1.1.0,<2.0.0

**4.0.0 - 07/03/2025**

 - Remove get_from_layer() method

**3.2.0 - 04/03/2025**

 - Bugfix: Raise a MissingLayerError if a requested value exists but not at the requested layer.
 - Get nested values from a single 'get' or 'get_tree' call
 - Move tree.get_from_layer() logic into tree.get() and add deprecation warning. 
 - Utilize centralized build tools

**3.1.0 - 03/18/2025**

 - Raise an error if YAML contains duplicate keys within the same level

**3.0.0 - 02/18/2025**

 - Better handle dunder-style keys

**2.2.1 - 12/27/2024**

 - Bugfix: failing mypy

**2.2.0 - 11/21/2024**

 - Drop support for Python 3.9

**2.1.0 - 10/31/2024**

 - Add getter methods

**2.0.2 - 08/01/2024**

 - Create explicit iterator for LayeredConfigTree

**2.0.1 - 06/14/2024**

 - Add py.typed marker

**2.0.0 - 05/17/2024**

 - Drop support for Python v3.8
 - Add type hints

**1.0.2 - 04/26/2024**

 - Allow default None argument for ConfigurationError

**1.0.1 - 04/11/2024**

 - Extract python version test matrix from python_versions.json
 - Automatically update README when supported python versions change
 - Bugfix missing ConfigurationError attribute

**1.0.0 - 04/11/2024**

 - Initial release
