**1.0.1 - 11/05/26**

- Read the installed packages with ``importlib.metadata`` in ``pip_env.validate``, so
  psimulate runs from a venv without ``pip``. Names are compared in normalized form, so
  a ``requirements.txt`` that ``pip`` wrote still matches on restart

**1.0.0 - 11/04/26**

- Initial release: ``psimulate`` and ``vipin`` split out of ``vivarium-cluster-tools`` into their own package
