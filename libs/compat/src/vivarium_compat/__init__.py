"""vivarium-compat is retired.

This package existed as a transitional artifact for the vivarium monorepo
migration: it installed a ``.pth``-based import hook at Python startup
that redirected old top-level import paths (``vivarium_profiling``,
``risk_distributions``, ``gbd_mapping``, ``gbd_mapping_generator``) to
their ``vivarium.<subpkg>`` equivalents in the monorepo.

As of version 0.7.0 the hook is gone. The ``.pth`` file is a comment-only
no-op; ``import vivarium_compat`` still succeeds but does nothing.

Migrate any remaining ``import <old_name>`` statements in your code to
``import vivarium.<new_name>``.

This package will not receive further releases. Please remove it from
your project dependencies.
"""
