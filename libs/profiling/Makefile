# Check if we're running in Jenkins
ifdef JENKINS_URL
# 	Files are already in workspace from shared library
	MAKE_INCLUDES := .
else
# 	For local dev, use the installed vivarium_build_utils package if it exists
# 	First, check if we can import vivarium_build_utils and assign 'yes' or 'no'.
# 	We do this by importing the package in python and redirecting stderr to the null device.
# 	If the import is successful (&&), it will print 'yes', otherwise (||) it will print 'no'.
	VIVARIUM_BUILD_UTILS_AVAILABLE := $(shell python -c "import vivarium_build_utils" 2>/dev/null && echo "yes" || echo "no")
# 	If vivarium_build_utils is available, get the makefiles path or else set it to empty
	ifeq ($(VIVARIUM_BUILD_UTILS_AVAILABLE),yes)
		MAKE_INCLUDES := $(shell python -c "from vivarium_build_utils.resources import get_makefiles_path; print(get_makefiles_path())")
	else
		MAKE_INCLUDES :=
	endif
endif

# Set the package name as the last part of this file's parent directory path
PACKAGE_NAME = $(notdir $(CURDIR))

# Helper function for validating enum arguments
validate_arg = $(if $(filter-out $(2),$(1)),$(error Error: '$(3)' must be one of: $(2), got '$(1)'))

ifneq ($(MAKE_INCLUDES),) # not empty
# Include makefiles from vivarium_build_utils
include $(MAKE_INCLUDES)/base.mk
include $(MAKE_INCLUDES)/test.mk
else # empty
# Use this help message (since the vivarium_build_utils version is not available)
help:
	@echo
	@echo "For Make's standard help, run 'make --help'."
	@echo
	@echo "Most of our Makefile targets are provided by the vivarium_build_utils"
	@echo "package. To access them, you need to create a development environment first."
	@echo
	@echo "make build-env"
	@echo
	@echo "USAGE:"
	@echo "  make build-env [type=<environment type>] [name=<environment name>] [py=<python version>] [include_timestamp=<yes|no>] [lfs=<yes|no>]"
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
	@echo
	@echo "After creating the environment:"
	@echo "  1. Activate it: 'conda activate <environment_name>'"
	@echo "  2. Run 'make help' again to see all newly available targets"
	@echo
endif

build-env: # Create a new environment with installed packages
#	Validate arguments - exit if unsupported arguments are passed
	@allowed="type name lfs py include_timestamp"; \
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
	
	conda create -n $(name) python=$(py) --yes
# 	Bootstrap vivarium_build_utils into the new environment
	conda run -n $(name) pip install vivarium_build_utils
#	Install packages based on type
	@if [ "$(type)" = "simulation" ]; then \
		conda run -n $(name) make install ENV_REQS=dev; \
		conda install -n $(name) redis -c anaconda -y; \
	elif [ "$(type)" = "artifact" ]; then \
		conda run -n $(name) make install ENV_REQS=data; \
	fi
	@if [ "$(lfs)" = "yes" ]; then \
		conda run -n $(name) conda install -c conda-forge git-lfs --yes; \
		conda run -n $(name) git lfs install; \
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
