---
name: make-commands
description: Reference for the shared `make` targets used across vivarium repositories, centralized in `vivarium/build_utils/resources/makefiles/`. Use this when the task involves making a new conda environment, formatting code, running development checks, or type hinting. Use whenever the user asks what a `make <target>` does in a vivarium repo, mentions running `make` in this ecosystem, or whenever another skill or instruction says "run `make X`" and you need to know exactly what it does, its arguments, env vars, and side effects.
---

# Vivarium `make` commands

## How the system works

Every vivarium repo's top-level `Makefile` is a thin shim. In a local dev shell it imports two shared makefiles from the installed `vivarium.build_utils` package:

```make
include $(MAKE_INCLUDES)/base.mk
include $(MAKE_INCLUDES)/test.mk
```

Both files live at `vivarium/build_utils/resources/makefiles/`. That means **the same set of `make` targets is available in essentially every vivarium library**. When you see `make <target>` in a vivarium context, look here.

The only target that lives in the *downstream* `Makefile` itself is `build-env`, because it has to bootstrap `vivarium_build_utils` into a fresh conda env before the shared `base.mk` can be loaded. Under Jenkins (`$JENKINS_URL` set), the makefiles are pulled from the workspace instead of from an installed package.

`PACKAGE_NAME` is automatically set to the current directory's basename, so the targets reorient themselves correctly per-repo.

## Quickly figuring out which target the user wants

`make` (no args) prints a generated list of every defined target with its inline `#` description. 
`make help` prints a curated, grouped help message.


## Things to know that surprise people

- **`lint` doesn't fix anything.** It checks. To actually format, run `format`.
- **`make` with no args runs `list`**, not `help`. `.DEFAULT_GOAL := list` is set in `base.mk`.
- **`make install` re-runs `setup-slack` every time.** Idempotent, but expect to see Slack bot config output.
- **`test-*` targets read `RUNSLOW`/`RUNWEEKLY` as `make` variables**, not pytest CLI flags. `make test-all --runslow` will not work; `make test-all RUNSLOW=true` will.
- **Under Jenkins**, `MAKE_INCLUDES := .` — Jenkins copies `base.mk`/`test.mk` into the workspace ahead of time. In local dev, they're loaded from the installed `vivarium.build_utils` Python package via `get_makefiles_path()`.
- **`make help` and `make --help` are different.** The latter is the built-in GNU Make help, which just lists global options and variables. The former is a custom target that prints a curated message about the available targets in this ecosystem.

## When the user asks "what does `make X` do?"

1. Run make help and look for the target in the output. The inline descriptions are usually enough to answer the question, but if not, you can also:
2. Check the local `Makefile` in the user's CWD — downstream repos sometimes add repo-specific targets on top of the shared base.
3. Read `vivarium/build_utils/resources/makefiles/base.mk` and `test.mk` directly — those are the source of truth and may have changed since this skill was written.
