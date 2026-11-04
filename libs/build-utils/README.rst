====================
Vivarium Build Utils
====================

Vivarium Build Utils contains shared build utilities for Simulation Science projects.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-build-utils`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-build-utils

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite/libs/build-utils
   conda create -n ENVIRONMENT_NAME
   pip install -e .

Overview
========

This repository provides:

- **`vars/`**: Jenkins shared library functions for continuous integration pipelines
- **`resources/`**: Shared Makefiles and build scripts for consistent build processes

Note: for help with the Make targets available to any environment with this repository
installed, run `make help` in the terminal.

Monorepo support
================

``vivarium-build-utils`` supports both standalone repos and monorepos where many
packages live under ``libs/<pkg>/``. Standalone repos keep working with no changes;
the sections below describe what's needed for a monorepo.

Top-level Jenkinsfile (provisioner)
-----------------------------------

The monorepo's root ``Jenkinsfile`` calls ``monorepo()`` to provision a
Multibranch Pipeline for each per-package Jenkinsfile. Run this on the default
branch only::

  @Library('vivarium_build_utils') _

  monorepo(
      jenkinsfiles: [
          'libs/core/Jenkinsfile',
          'libs/public-health/Jenkinsfile',
      ],
      // Jenkins credential ID for the GitHub App. Required, no default; vbu
      // stays org-agnostic so the literal UUID lives next to the org context.
      githubCredentialsId: 'fad62062-b1f4-447b-997f-005d6b1ea41e',
      folderPrefix: 'Public',  // optional, defaults to "Public"
  )

The provisioned pipelines land under ``<folderPrefix>/<repo>/libs/<pkg>/``.

Per-package Jenkinsfile
-----------------------

Each ``libs/<pkg>/Jenkinsfile`` calls ``reusable_pipeline()`` the same way a
standalone repo would, with one new argument::

  @Library('vivarium_build_utils') _

  reusable_pipeline(
      test_types: ['unit', 'integration'],
      deployable: true,
      env_reqs: 'ci_jenkins',  // pyproject.toml extra to install
  )

``env_reqs`` selects which ``[project.optional-dependencies]`` extra ``make
install`` pulls in. Omit it (or leave empty) on standalone repos to keep
base.mk's default of ``dev``.

Deployable callers (``deployable: true``) can also pass
``github_credentials_id: '<jenkins-credential-id>'`` to override the git
credential used at deploy time for pushing the release tag. When omitted, the
deploy stage falls back to the credential configured on the Multibranch
Pipeline's branch source, which is the right default for most repos.

Tag prefix
----------

The ``TAG_PREFIX`` environment variable controls ``make tag-version``,
``make validate-tag``, and ``make check-release-tag``. It must be set consistently
in all of them, or ``validate-tag`` will silently look at the wrong set of tags.

- Standalone repos: leave unset. Tags are ``v<X.Y.Z>``.
- Monorepo libs: set ``TAG_PREFIX=vivarium-<lib>-`` (e.g. ``vivarium-core-``).
  Tags become ``vivarium-<lib>-v<X.Y.Z>``.

Release workflows that invoke ``make validate-tag`` or ``make tag-version``
should export ``TAG_PREFIX`` before running them.

Beta releases
-------------

A CHANGELOG entry of ``**X.Y.ZbN - MM/DD/YY**`` releases a beta, for example
``**1.3.0b1 - 11/03/26**``. Beta is the only supported pre-release.
``make validate-tag`` rejects other forms such as ``1.3.0a1``, ``1.3.0rc1``, and
``1.3.0-beta.1``. It also checks the step from the versions already released:

- final to final: one patch, minor, or major step, as before.
- final to beta: ``b1`` of a version one step from the last final
  (``1.2.0 -> 1.3.0b1``).
- beta to beta: the next beta of the same version (``1.3.0b1 -> 1.3.0b2``).
- beta to final: the final of that version (``1.3.0b2 -> 1.3.0``).

A beta is not released after its final.

The Jenkins deploy runs ``make validate-tag`` before ``make tag-version``. Jenkins
sets no release tag, so the target checks the tag ``tag-version`` would create,
``${TAG_PREFIX}v${PACKAGE_VERSION}``. This also applies the one-step rule to final
releases deployed by Jenkins, which it did not check before.

``PACKAGE_VERSION`` keeps the ``bN`` suffix, so ``make tag-version`` tags
``v1.3.0b1`` and the Jenkins changelog check sees ``1.3.0b1 -> 1.3.0b2`` as a
version update. ``make deploy-docs`` publishes a beta's docs under its version but
leaves the ``current`` link on the last final.

pip and uv install a beta only when it is requested by version
(``pip install <dist>==1.3.0b1``) or with ``--pre``, so a plain install keeps
getting the last final.

Fetching from internal Artifactory
----------------------------------

``IHME_PYPI`` defaults to the internal Artifactory URL and is woven into
``EXTRA_INDEX_FLAGS`` for ``make install``. Override it to empty
(``make install IHME_PYPI=``) in environments that can't reach IHME's network
(e.g. GitHub Actions runners). ``make deploy-package-artifactory`` requires a
non-empty ``IHME_PYPI`` and is Jenkins/internal-only.

Cross-library PRs
-----------------

A single PR can modify several interdependent monorepo libraries, including
bumping one and consuming the new version of another even though the upstream's
dependency on it still resolves against PyPI (where the new version
isn't released yet). ``make install CHANGED_LIBS="<lib1> <lib2> ..."`` opts a build
into in-tree resolution where any ``CHANGED_LIBS`` (libraries whose source changed
in the PR) that are also (1) reachable from the package being built and (2) whose
pending ``CHANGELOG.rst`` version satisfies the dependents' pins are installed
editably from local source (at the pending version) with a single ``uv`` invocation.
Unchanged dependencies still resolve from PyPI. 

``CHANGED_LIBS`` is a no-op when empty, so single-package installs are unaffected.

The GitHub Actions CI and release workflows wire this automatically.

When a build deploys
====================

This applies to repos that pass ``deployable: true``; monorepo libs release
through GitHub Actions instead and never reach the Jenkins deploy stage.

Only a build **Jenkins started because it found a new commit** deploys on its
own. If you do want to release from a build you started by hand, set
``FORCE_DEPLOY``; that is also how to redrive a deploy that failed partway
through.

A deploy requires all of:

- ``deployable: true``
- the ``main`` branch
- a deployable change since the last successful build
- a new-commit build or ``FORCE_DEPLOY``
- a version update in ``CHANGELOG.rst`` that wasn't already released from another commit
- every check in this build passed

Failing checks
==============

By default a check that fails (lint, mypy, tests, docs, doctests) marks its stage
and the build as failed but does not stop the build. Every check runs on every
Python version, so one build shows everything that is failing. ``make check`` and
``make lint`` behave the same way locally and end with a summary of which steps
failed. Pass ``fail_fast=1`` (or ``FAIL_FAST=1``) to either one to stop at the
first failure.

Set the ``FAIL_FAST`` build parameter to stop at the first failing check and abort
the other Python versions. Jenkins adds a new parameter to a job only after the
first build that declares it, so ``FAIL_FAST`` appears in "Build with Parameters"
after the job's first build on a library version that includes it. That first
build uses the default and runs every check.
