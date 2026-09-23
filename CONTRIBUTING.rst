Contributing
============

When contributing to this repository, please first discuss the change you wish to make via issue,
email, or any other method with the owners of this repository before making a change.

Please note we have a code of conduct, please follow it in all your interactions with the project.

Development Setup
-----------------

See the `README <README.md>`_ for instructions on setting up a local development environment and
installing packages in editable mode.

New packages must include a ``python_versions.json`` file at the package root listing the Python
versions to test against, e.g.::

    ["3.11", "3.12"]

Every version in that file gates a merge; they become entries in the package's CI
matrix and the last entry is the canonical one used for deploys. Note that the docs
build is separate and only runs on a single hard-coded version of python.

Candidate Versions
~~~~~~~~~~~~~~~~~~

A package can also name Python versions it wants exercised *before* committing to support them
in its own ``pyproject.toml``, e.g.::

    [tool.vivarium.python-support]
    candidates = ["3.14"]

These never run on a pull request. The `Candidate Check
<https://github.com/ihmeuw/vivarium-suite/actions/workflows/candidate-check.yml>`_ workflow is on a schedule and runs each
declaring package's full check against each of its candidates on ``main``, and that is the
only place they run; a version the ecosystem is not ready for is visible when you look for it
and cannot fail anyone's build in the meantime.

To promote a candidate, open an ordinary PR that moves the version into ``python_versions.json``
(thus making it tested as part of the PR), removes it from ``candidates``, and carries whatever
fixes the check surfaced. From then on it gates like any other supported version, and the ``run-downstream``
label covers the package's in-tree dependents.

Type Hinting
------------

Packages that are fully typed must include a ``py.typed`` marker file in their source tree (per
PEP 561). CI will run ``mypy`` only for packages that have this marker.

Submitting Changes
------------------

- Always make a new branch for your work.
- Patches should be small to facilitate easier review. Sometimes this will result in many small
  PRs to land a single large feature.
- Larger changes should be discussed in the project's GitHub issues page.
- The PR template asks for a Jira (MIC) ticket link. Contributors outside the Simulation Science
  team can link the GitHub issue they opened instead.
- New features and significant bug fixes should be documented in the changelog.
- You must have legal permission to distribute any code you contribute to ``vivarium-suite``, and it
  must be available under the BSD-3-Clause license.

Changelog Format
----------------

Each package under ``libs/`` and each Claude Code plugin under ``tools/`` maintains its own
``CHANGELOG.rst``. The release workflows parse the first line of this file to determine the
version and date. The expected format, with a two-digit year, is::

    **X.Y.Z - MM/DD/YY**

For example::

    **4.1.0 - 04/28/26**

A four-digit year fails the workflow's date check. A release is triggered automatically when a
``CHANGELOG.rst`` is updated on ``main`` and the parsed tag does not already exist. The date must
match the day the change lands on ``main`` (Pacific time), not the day the PR was opened. See
``.github/workflows/release.yml`` (libraries) and ``.github/workflows/release-ai-tools*.yml``
(plugins) for details.
