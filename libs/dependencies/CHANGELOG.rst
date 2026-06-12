**1.1.0 - 06/11/26**

Initial release from the vivarium-suite monorepo; the standalone ``ihmeuw/vivarium_dependencies``
repository has been archived.

- No dependency or extras-shape changes for downstream consumers.
  ``vivarium-dependencies[<extra>]`` references in sibling ``pyproject.toml``
  files continue to resolve unchanged.
- Replaced ``setup.py`` with a PEP 621 ``pyproject.toml`` (build backend and
  extras unchanged).
- Scrubbed standalone scaffolding (``__about__.py``, ``setup.py``,
  ``CODE_OF_CONDUCT.rst``, ``CONTRIBUTING.rst``, ``.github/``).

**1.0.8 - 06/09/26**

 - Replace networkx-stubs with types-networkx in the networkx extra

**1.0.7 - 04/14/26**
 
 - Add vbu pin

**1.0.6 - 03/16/26**
 
 - Validate version and CHANGELOG date prior to deploying

**1.0.5 - 01/21/26**
 
 - Pin pandas<3.0.0

**1.0.4 - 1/13/26**

 - Pin scipy<1.17.0

**1.0.3 - 1/12/26**

 - Pin sphinx<9.0.0

**1.0.2 - 12/10/25**

 - Add dependency: sphinx-rtd-theme>=2.0.0

**1.0.1 - 8/1/25**

 - Add dependency: numpy<2.0.0

**1.0.0 - 7/31/25**

 - Initial release

**0.1.0 - 7/29/25**

 - Initial release candidate
