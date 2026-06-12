# Check if we're running in Jenkins
ifdef JENKINS_URL
# 	Files are already in workspace from shared library
	MAKE_INCLUDES := .
else
# 	For local dev, use the installed vivarium_build_utils package if it exists.
	VIVARIUM_BUILD_UTILS_AVAILABLE := $(shell python -c "import vivarium_build_utils" 2>/dev/null && echo "yes" || echo "no")
	ifeq ($(VIVARIUM_BUILD_UTILS_AVAILABLE),yes)
		MAKE_INCLUDES := $(shell python -c "from vivarium_build_utils.resources import get_makefiles_path; print(get_makefiles_path())")
	else
		MAKE_INCLUDES :=
	endif
endif

# Set the package name as the last part of this file's parent directory path
PACKAGE_NAME = $(notdir $(CURDIR))

# vivarium-dependencies is a code-less metapackage: it has no Python source,
# no per-package Python-version matrix, and no need for the `build-env`
# bootstrap target the sibling libs define here. We just pull in vbu's
# install/test targets when vbu is available; otherwise the help target
# below tells you how to install vbu.
ifneq ($(MAKE_INCLUDES),) # not empty
include $(MAKE_INCLUDES)/base.mk
include $(MAKE_INCLUDES)/test.mk
else # empty
help:
	@echo
	@echo "vivarium_build_utils is not installed in this environment."
	@echo "Install it first, then re-run 'make help' to see available targets:"
	@echo
	@echo "  pip install vivarium_build_utils"
	@echo
endif
