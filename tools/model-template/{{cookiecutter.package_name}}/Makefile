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

# Macro for validating make target arguments
# Usage: $(call validate_make_args,target_name,allowed_args)
# Example: $(call validate_make_args,build-env,type name path)
define validate_make_args
	@allowed="$(2)"; \
	for arg in $(filter-out $(1),$(MAKECMDGOALS)) $(MAKEFLAGS); do \
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
endef

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
	@echo "================================================================================"
	@echo "build-env: Create a full conda environment from scratch"
	@echo "================================================================================"
	@echo
	@echo "This target creates a new conda environment and installs all required"
	@echo "packages for development or artifact generation, depending on the 'type' argument."
	@echo "It is recommended to use this target only if you cannot use the 'build-shared-env' target,"
	@echo "either because you are not on the cluster or because you need to customize the environment,"
	@echo "particularly if you need non-python packages installed via conda."
	@echo
	@echo "USAGE:"
	@echo "  make build-env [type=<environment type>] [name=<environment name>] [path=<environment path>] [py=<python version>] [include_timestamp=<yes|no>] [lfs=<yes|no>] [force=<yes|no>]"
	@echo
	@echo "ARGUMENTS:"
	@echo "  type [optional]"
	@echo "      Type of conda environment. Either 'simulation' (default) or 'artifact'"
	@echo "  name [optional]"
	@echo "      Name of the conda environment to create (defaults to <PACKAGE_NAME>_<TYPE>)"
	@echo "  path [optional]"
	@echo "      Absolute path where the environment should be created (overrides name for location)"
	@echo "  include_timestamp [optional]"
	@echo "      Whether to append a timestamp to the environment name. Either 'yes' or 'no' (default)"
	@echo "  lfs [optional]"
	@echo "      Whether to install git-lfs in the environment. Either 'yes' or 'no' (default)"
	@echo "  py [optional]"
	@echo "      Python version (defaults to latest supported)"
	@echo "  force [optional]"
	@echo "      Whether to remove and recreate an existing environment. Either 'yes' or 'no' (default)"
	@echo
	@echo "After creating the environment:"
	@echo "  1. Activate it: 'conda activate <environment_name>'"
	@echo "  2. Run 'make help' again to see all newly available targets"
	@echo
	@echo "================================================================================"
	@echo "build-shared-env: Create a lightweight venv on top of a shared conda environment"
	@echo "================================================================================"
	@echo
	@echo "This is the RECOMMENDED approach for development on the cluster. It creates a virtual"
	@echo "environment that inherits packages from a Jenkins-built shared conda environment,"
	@echo "while allowing you to install the local package in editable mode."
	@echo
	@echo "USAGE:"
	@echo "  make build-shared-env [type=<environment type>] [venv_dir=<directory>] [venv_name=<name>] [shared_env_dir=<path>] [force=<yes|no>]"
	@echo
	@echo "ARGUMENTS:"
	@echo "  type [optional]"
	@echo "      Type of shared environment to use. Either 'simulation' (default) or 'artifact'"
	@echo "  venv_dir [optional]"
	@echo "      Directory where venvs are stored (defaults to '.venv')"
	@echo "  venv_name [optional]"
	@echo "      Name of the venv to create (defaults to '<PACKAGE_NAME>_<TYPE>')"
	@echo "  shared_env_dir [optional]"
	@echo "      Base directory for shared environments (defaults to Jenkins shared env location)"
	@echo "  force [optional]"
	@echo "      Whether to remove and recreate an existing venv. Either 'yes' or 'no' (default)"
	@echo
	@echo "After creating the environment:"
	@echo "  1. Activate it: 'source <venv_dir>/<environment_name>/bin/activate'"
	@echo "  2. Run 'make help' again to see all available targets"
	@echo
endif

build-env: # Create a new environment with installed packages
#	Validate arguments - exit if unsupported arguments are passed
	$(call validate_make_args,build-env,type name path lfs py include_timestamp force)
	
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
#	path (optional - if set, use -p for conda create instead of -n)
	@$(eval path ?=)
#	lfs
	@$(eval lfs ?= no)
	@$(call validate_arg,$(lfs),yes no,lfs)
#	force
	@$(eval force ?= no)
	@$(call validate_arg,$(force),yes no,force)
#	python version
	@$(eval py ?= $(shell cat python_versions.json | tr -d '[]" ' | tr ',' '\n' | sort -t. -k1,1n -k2,2n | tail -1))
#	Determine conda create flag: -p for path, -n for name
	@$(eval CONDA_CREATE_FLAG := $(if $(path),-p $(path),-n $(name)))
#	Determine conda run flag: -p for path, -n for name
	@$(eval CONDA_RUN_FLAG := $(if $(path),-p $(path),-n $(name)))

#	Check if environment already exists and handle based on force flag
	@if conda env list | grep -qE "$(if $(path),^$(path),^$(name))\s"; then \
		if [ "$(force)" = "yes" ]; then \
			echo "Removing existing environment..."; \
			conda remove $(CONDA_CREATE_FLAG) --all --yes; \
		else \
			echo "Error: Environment already exists at $(if $(path),$(path),$(name))" >&2; \
			echo "Use 'force=yes' to remove and recreate it, or specify a different location with 'name=<name>' or 'path=<path>'" >&2; \
			exit 1; \
		fi \
	fi

	conda create $(CONDA_CREATE_FLAG) python=$(py) --yes
# 	Bootstrap vivarium_build_utils into the new environment.
	conda run $(CONDA_RUN_FLAG) pip install "vivarium_build_utils>=4.0.0,<5.0.0"
#	Install packages based on type
	@if [ "$(type)" = "simulation" ]; then \
		conda run $(CONDA_RUN_FLAG) make install ENV_REQS=dev; \
		conda install $(CONDA_RUN_FLAG) redis -c anaconda -y; \
	elif [ "$(type)" = "artifact" ]; then \
		conda run $(CONDA_RUN_FLAG) make install ENV_REQS=data; \
	fi
	@if [ "$(lfs)" = "yes" ]; then \
		conda run $(CONDA_RUN_FLAG) conda install -c conda-forge git-lfs --yes; \
		conda run $(CONDA_RUN_FLAG) git lfs install; \
	fi

	@echo
	@echo "Finished building environment"
	@$(if $(path),echo "  path: $(path)",echo "  name: $(name)")
	@echo "  type: $(type)"
	@echo "  git-lfs installed: $(lfs)"
	@echo "  python version: $(py)"
	@echo "  forced rebuild: $(force)"
	@echo
	@echo "After creating the environment:"
	@$(if $(path),echo "  1. Activate it: 'conda activate $(path)'",echo "  1. Activate it: 'conda activate $(name)'")
	@echo "  2. Run 'make help' again to see all newly available targets"
	@echo

# Default shared environment directory (set by Jenkins nightly builds)
SHARED_ENV_DIR ?= /mnt/team/simulation_science/priv/engineering/jenkins/shared_envs

build-shared-env: # Create a lightweight venv overlay on top of a shared conda environment
#	Validate arguments - exit if unsupported arguments are passed
	$(call validate_make_args,build-shared-env,type venv_dir venv_name shared_env_dir force)

#	Handle arguments and set defaults
#	type
	@$(eval type ?= simulation)
	@$(call validate_arg,$(type),simulation artifact,type)
#	venv_dir
	@$(eval venv_dir ?= .venv)
#	venv_name
	@$(eval venv_name ?= $(PACKAGE_NAME)_$(type))
#	Construct full venv path
	@$(eval venv_path := $(venv_dir)/$(venv_name))
#	shared_env_dir
	@$(eval shared_env_dir ?= $(SHARED_ENV_DIR))
#	force
	@$(eval force ?= no)
	@$(call validate_arg,$(force),yes no,force)
#	Construct shared environment path
	@$(eval SHARED_ENV_NAME := $(PACKAGE_NAME)_$(type)_current)
	@$(eval SHARED_ENV_PATH := $(shared_env_dir)/$(SHARED_ENV_NAME))

#	Verify shared environment exists
	@if [ ! -d "$(SHARED_ENV_PATH)" ]; then \
		echo "Error: Shared environment not found at $(SHARED_ENV_PATH)" >&2; \
		echo "Make sure the Jenkins nightly build has run successfully." >&2; \
		exit 1; \
	fi

#	Handle existing venv
	@if [ -d "$(venv_path)" ]; then \
		if [ "$(force)" = "yes" ]; then \
			echo "Clearing existing venv at $(venv_path)"; \
			rm -rf "$(venv_path)"; \
		else \
			echo "Warning: venv already exists at $(venv_path)" >&2; \
			echo "Use 'force=yes' to remove and recreate it, or specify a different location with 'venv_dir=<dir>' and 'venv_name=<name>'" >&2; \
			exit 1; \
		fi \
	fi

#	Create venv overlay with system-site-packages
	@echo "Creating venv overlay at $(venv_path)"
	@echo "  Base environment: $(SHARED_ENV_PATH)"
	$(SHARED_ENV_PATH)/bin/python -m venv --system-site-packages $(venv_path)

#	Patch activate scripts to include shared environment's bin/ in PATH
#	This ensures CLI entry points (e.g. psimulate) from the shared env are available.
#	We append to the activate script rather than sed-replacing the PATH line because
#	the exact format of that line varies across Python versions.
#	_OLD_VIRTUAL_PATH is set by the activate script before any PATH modification,
#	so we can reconstruct PATH with the correct precedence:
#	  venv bin > shared env bin > original PATH
	@echo "Patching activate scripts to inherit shared environment CLI entry points"
	@echo '' >> $(venv_path)/bin/activate
	@echo '# Include shared environment CLI entry points (e.g. psimulate)' >> $(venv_path)/bin/activate
	@echo 'PATH="$$VIRTUAL_ENV/bin:$(SHARED_ENV_PATH)/bin:$${_OLD_VIRTUAL_PATH}"' >> $(venv_path)/bin/activate
	@echo 'export PATH' >> $(venv_path)/bin/activate
	@if [ -f "$(venv_path)/bin/activate.fish" ]; then \
		echo '' >> $(venv_path)/bin/activate.fish; \
		echo '# Include shared environment CLI entry points (e.g. psimulate)' >> $(venv_path)/bin/activate.fish; \
		echo 'set -gx PATH "$$VIRTUAL_ENV/bin" "$(SHARED_ENV_PATH)/bin" $$_OLD_VIRTUAL_PATH' >> $(venv_path)/bin/activate.fish; \
	fi
	@if [ -f "$(venv_path)/bin/activate.csh" ]; then \
		echo '' >> $(venv_path)/bin/activate.csh; \
		echo '# Include shared environment CLI entry points (e.g. psimulate)' >> $(venv_path)/bin/activate.csh; \
		echo 'setenv PATH "$$VIRTUAL_ENV/bin:$(SHARED_ENV_PATH)/bin:$$_OLD_VIRTUAL_PATH"' >> $(venv_path)/bin/activate.csh; \
	fi

#	Install local package in editable mode (no-deps since shared env has dependencies)
	@echo "Installing local package in editable mode (--no-deps)"
	$(venv_path)/bin/pip install -e . --no-deps

	@echo
	@echo "Finished creating venv"
	@echo "  venv directory: $(venv_dir)"
	@echo "  venv name: $(venv_name)"
	@echo "  full path: $(venv_path)"
	@echo "  base environment: $(SHARED_ENV_PATH)"
	@echo "  type: $(type)"
	@echo
	@echo "After creating the environment:"
	@echo "  1. Activate it: 'source $(venv_path)/bin/activate'"
	@echo "  2. Run 'make help' again to see all newly available targets"
	@echo
