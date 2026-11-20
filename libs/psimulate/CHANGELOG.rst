**1.1.1 - 11/19/26**

- Raise ``BackupLoadError`` when a backup exists but cannot be loaded, instead of
  restarting the simulation from the beginning
- Start from the beginning only when there is no backup metadata or no backup file
  for the job. Other errors while looking for a backup now fail the job instead of
  restarting it
- Require ``vivarium-engine`` 6.1.1 or later, which fixes loading backups

**1.1.0 - 11/17/26**

- Require ``vivarium-dependencies>=2.0.0``, which raises the floors of this library's
  dependencies to ``click>=8.4.0``, ``loguru>=0.7.2``, ``numpy>=2.1.0``,
  ``pandas>=3.0.0``, ``pyarrow>=18.0.0``, ``tables>=3.10.1``

**1.0.0 - 11/04/26**

- Initial release: ``psimulate`` and ``vipin`` split out of ``vivarium-cluster-tools`` into their own package
