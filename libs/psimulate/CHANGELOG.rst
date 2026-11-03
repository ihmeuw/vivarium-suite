**1.0.0 - 11/03/26**

- Initial release: ``psimulate`` and ``vipin`` split out of ``vivarium-cluster-tools`` into their own package
- Finish runs started under ``vivarium-cluster-tools`` 4.x before upgrading. The worker's
  module path changed, so ``psimulate restart`` of such a run after the upgrade is not supported
