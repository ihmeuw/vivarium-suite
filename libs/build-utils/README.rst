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

Scheduled builds and deploys
----------------------------

Branches listed in ``scheduled_branches`` get a nightly cron trigger. The
trigger is a ``parameterizedCron`` (Jenkins' `Parameterized Scheduler
<https://plugins.jenkins.io/parameterized-scheduler/>`_ plugin, which must be
installed on the Jenkins controller) that supplies ``SKIP_DEPLOY=true``, so
nightly builds of the default branch never deploy. The parameter is recorded on
the build, so a ``Rerun`` of a nightly inherits it.

Deploys are reserved for builds Jenkins starts from a push. Any build a person
starts in the UI — ``Build with Parameters``, ``Rerun``, or ``Replay`` — skips
the deploy stage whatever its parameters say, so investigating a failed nightly
cannot publish a release. Set ``FORCE_DEPLOY`` to release by hand, which is how
to redrive a deploy that failed partway.

A deploy therefore requires all of: ``deployable: true``, the ``main`` branch,
a push-started build (or ``FORCE_DEPLOY``), ``SKIP_DEPLOY`` unset, and a
deployable change in the tip commit.

Jenkins registers a job's triggers from its last build, so the
``parameterizedCron`` trigger replaces the old one only once a scheduled branch
has built again under this version. Trigger one build per scheduled branch after
upgrading, so that no nightly fires from the stale trigger without
``SKIP_DEPLOY``.

Tag prefix
----------

The ``TAG_PREFIX`` environment variable controls both ``make tag-version`` and
``make validate-tag``. It must be set consistently in both targets, or
``validate-tag`` will silently look at the wrong set of tags.

- Standalone repos: leave unset. Tags are ``v<X.Y.Z>``.
- Monorepo libs: set ``TAG_PREFIX=vivarium-<lib>-`` (e.g. ``vivarium-core-``).
  Tags become ``vivarium-<lib>-v<X.Y.Z>``.

Release workflows that invoke ``make validate-tag`` or ``make tag-version``
should export ``TAG_PREFIX`` before running them.

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
