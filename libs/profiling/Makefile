# Check if we're running in Jenkins
ifdef JENKINS_URL
# 	Files are already in workspace from shared library
	MAKE_INCLUDES := .
else
# 	For local dev, use the installed vivarium.build_utils package if it exists
# 	First, check if we can import vivarium.build_utils and assign 'yes' or 'no'.
# 	We do this by importing the package in python and redirecting stderr to the null device.
# 	If the import is successful (&&), it will print 'yes', otherwise (||) it will print 'no'.
	VIVARIUM_BUILD_UTILS_AVAILABLE := $(shell python -c "import vivarium.build_utils" 2>/dev/null && echo "yes" || echo "no")
# 	If vivarium.build_utils is available, get the makefiles path or else set it to empty
	ifeq ($(VIVARIUM_BUILD_UTILS_AVAILABLE),yes)
		MAKE_INCLUDES := $(shell python -c "from vivarium.build_utils.resources import get_makefiles_path; print(get_makefiles_path())")
	else
		MAKE_INCLUDES :=
	endif
endif

# Set the package name as the last part of this file's parent directory path
PACKAGE_NAME = $(notdir $(CURDIR))

# Helper function for validating enum arguments
validate_arg = $(if $(filter-out $(2),$(1)),$(error Error: '$(3)' must be one of: $(2), got '$(1)'))

ifneq ($(MAKE_INCLUDES),) # not empty
# Include makefiles from vivarium.build_utils
include $(MAKE_INCLUDES)/base.mk
include $(MAKE_INCLUDES)/test.mk
else # empty
# Use this help message (since the vivarium.build_utils version is not available)
help:
	@echo
	@echo "For Make's standard help, run 'make --help'."
	@echo
	@echo "Most of our Makefile targets are provided by the vivarium.build_utils"
	@echo "package. To access them, you need to create a development environment first."
	@echo
	@echo "make build-env"
	@echo
	@echo "USAGE:"
	@echo "  make build-env [type=<environment type>] [name=<environment name>] [py=<python version>] [include_timestamp=<yes|no>] [lfs=<yes|no>] [artifactory_access=<yes|no>] [keep_env=<yes|no>]"
	@echo
	@echo "ARGUMENTS:"
	@echo "  type [optional]"
	@echo "      Type of conda environment. Either 'simulation' (default) or 'artifact'"
	@echo "  name [optional]"
	@echo "      Name of the conda environment to create (defaults to <PACKAGE_NAME>_<TYPE>)"
	@echo "  include_timestamp [optional]"
	@echo "      Whether to append a timestamp to the environment name. Either 'yes' or 'no' (default)"
	@echo "  lfs [optional]"
	@echo "      Whether to install git-lfs in the environment. Either 'yes' or 'no' (default)"
	@echo "  py [optional]"
	@echo "      Python version (defaults to latest supported)"
	@echo "  artifactory_access [optional]"
	@echo "      Whether the build host can reach the IHME artifactory. Either 'yes'"
	@echo "      (default) or 'no'; use 'no' to build off-VPN without artifactory."
	@echo "  keep_env [optional]"
	@echo "      Whether to keep a failed build's partial environment for debugging."
	@echo "      Either 'yes' or 'no' (default, i.e. tear it down)."
	@echo
	@echo "After creating the environment:"
	@echo "  1. Activate it: 'conda activate <environment_name>'"
	@echo "  2. Run 'make help' again to see all newly available targets"
	@echo
endif

build-env: # Create a new environment with installed packages
#	Validate arguments - exit if unsupported arguments are passed
	@allowed="type name lfs py include_timestamp artifactory_access keep_env"; \
	for arg in $(filter-out build-env,$(MAKECMDGOALS)) $(MAKEFLAGS); do \
		case $$arg in \
			*=*) \
				arg_name=$${arg%%=*}; \
				if ! echo " $$allowed " | grep -q " $$arg_name "; then \
					allowed_list=$$(echo $$allowed | sed 's/ /, /g'); \
					echo "Error: Invalid argument '$$arg_name'. Allowed arguments are: $$allowed_list" >&2; \
					exit 1; \
				fi \
				;; \
		esac; \
	done
	
#   Handle arguments and set defaults
#   type
	@$(eval type ?= simulation)
	@$(call validate_arg,$(type),simulation artifact,type)
#	name
	@$(eval name ?= $(PACKAGE_NAME)_$(type))
#	timestamp
	@$(eval include_timestamp ?= no)
	@$(call validate_arg,$(include_timestamp),yes no,include_timestamp)
	@$(if $(filter yes,$(include_timestamp)),$(eval override name := $(name)_$(shell date +%Y%m%d_%H%M%S)),)
#	lfs
	@$(eval lfs ?= no)
	@$(call validate_arg,$(lfs),yes no,lfs)
#	python version
	@$(eval py ?= $(shell python -c "import json; versions = json.load(open('python_versions.json')); print(max(versions, key=lambda x: tuple(map(int, x.split('.')))))"))
#	artifactory_access
	@$(eval artifactory_access ?= yes)
	@$(call validate_arg,$(artifactory_access),yes no,artifactory_access)
#	keep_env
	@$(eval keep_env ?= no)
	@$(call validate_arg,$(keep_env),yes no,keep_env)

#	Tear down on failure rather than leave behind an env that activates but has
#	nothing installed.
	@if ! $(MAKE) --no-print-directory _build-env \
			name=$(name) py=$(py) type=$(type) lfs=$(lfs) \
			artifactory_access=$(artifactory_access); then \
		echo >&2; \
		if [ "$(keep_env)" = "yes" ]; then \
			echo "Error: failed to build environment '$(name)'. Keeping it (keep_env=yes)." >&2; \
		else \
			echo "Error: failed to build environment '$(name)'. Removing it; pass keep_env=yes to inspect it instead." >&2; \
			conda env remove -n $(name) --yes; \
		fi; \
		exit 1; \
	fi

	@echo
	@echo "Finished building environment"
	@echo "  name: $(name)"
	@echo "  type: $(type)"
	@echo "  git-lfs installed: $(lfs)"
	@echo "  python version: $(py)"
	@echo
	@echo "Don't forget to activate it with: 'conda activate $(name)'"
	@echo

# Extras group that `type` selects from base.mk's `install` target.
ENV_REQS_FOR_TYPE = $(if $(filter artifact,$(type)),data,dev)

# Private: `build-env`'s steps, one per recipe line so make's fail-fast applies to each.
.PHONY: _build-env
_build-env:
	conda create -n $(name) python=$(py) --yes
# 	Bootstrap vivarium.build_utils into the new environment.
# 	--no-capture-output streams output, so a failure isn't hidden behind a long silence.
	conda run --no-capture-output -n $(name) pip install vivarium-build-utils
# 	artifactory_access=no passes an empty IHME_PYPI, which clears base.mk's EXTRA_INDEX_FLAGS
	conda run --no-capture-output -n $(name) make install ENV_REQS=$(ENV_REQS_FOR_TYPE) $(if $(filter no,$(artifactory_access)),IHME_PYPI=)
	@if [ "$(type)" = "simulation" ]; then \
		conda install -n $(name) redis -c anaconda -y; \
	fi
# 	`set -e` is load-bearing: a multi-command recipe line reports only its LAST
# 	command's status, which is what masked the failing install before.
	@if [ "$(lfs)" = "yes" ]; then \
		set -e; \
		conda run --no-capture-output -n $(name) conda install -c conda-forge git-lfs --yes; \
		conda run --no-capture-output -n $(name) git lfs install; \
	fi
# 	A fresh env's only editable install is this checkout, so an empty list means it never installed.
	@conda run --no-capture-output -n $(name) pip list --editable --format=freeze | grep -q . \
		|| { echo "Error: nothing was installed into '$(name)' from $(CURDIR)." >&2; exit 1; }
