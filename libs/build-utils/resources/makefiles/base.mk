SHELL := /bin/bash
UTILS_DIR := $(dir $(abspath $(dir $(abspath $(dir $(lastword $(MAKEFILE_LIST)))))))
.DEFAULT_GOAL := list # If someone runs "make", run "make list"

# Source files to format, lint, and type check.
LOCATIONS=src tests

# Unless overridden, build conda environment using the package name.
SAFE_NAME = $(shell python -c "from pkg_resources import safe_name; print(safe_name(\"$(PACKAGE_NAME)\"))")

setup_file   = $(shell find -name setup.py)
version_line = $(shell grep "version = " ${setup_file})
PACKAGE_VERSION = $(shell echo ${version_line} | cut -d "=" -f 2 | xargs)

# Use this URL to pull IHME Python packages and deploy this package to PyPi.
IHME_PYPI := https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/

# If CONDA_ENV_PATH is set (from a Jenkins build), use the -p flag when making Conda env in
# order to make env at specific path. Otherwise, make a named env at the default path using
# the -n flag.
PYTHON_VERSION ?= 3.11
CONDA_ENV_NAME ?= ${PACKAGE_NAME}_py${PYTHON_VERSION}
CONDA_ENV_CREATION_FLAG = $(if $(CONDA_ENV_PATH),-p ${CONDA_ENV_PATH},-n ${CONDA_ENV_NAME})

# These are the doc and source code files in this repo.
# When one of these files changes, it means that Make targets need to run again.
MAKE_SOURCES := $(shell find . -type d -name "*" ! -path "./.git*" ! -path "./.vscode" ! -path "./output" ! -path "./output/*" ! -path "./archive" ! -path "./dist" ! -path "./output/htmlcov*" ! -path "**/.pytest_cache*" ! -path "**/__pycache__" ! -path "./output/docs_build*" ! -path "./.pytype*" ! -path "." ! -path "./src/${PACKAGE_NAME}/legacy*" ! -path ./.history ! -path "./.history/*" ! -path "./src/${PACKAGE_NAME}.egg-info" ! -path ./.idea ! -path "./.idea/*" )

# Phony targets don't produce artifacts.
.PHONY: .list-targets build-env build-doc format integration build-package clean debug deploy-doc deploy-package full help list quick install install-upstream-deps

# List of Make targets is generated dynamically. To add description of target, use a # on the target definition.
list help: debug .list-targets

.list-targets: # Print available Make targets
	@echo
	@echo "Make targets:"
	@for file in Makefile $(UTILS_DIR)resources/makefiles/*.mk; do \
		grep -i "^[a-zA-Z][a-zA-Z0-9_ \.\-]*: .*[#].*" $$file | sort | sed 's/:.*#/ : /g'; \
	done | column -t -s:
	@echo

debug: # Print debug information (environment variables)
	@echo "'make' invoked with these environment variables:"
	@echo "CONDA_ENV_NAME:                   ${CONDA_ENV_NAME}"
	@echo "IHME_PYPI:                        ${IHME_PYPI}"
	@echo "LOCATIONS:                        ${LOCATIONS}"
	@echo "PACKAGE_NAME:                     ${PACKAGE_NAME}"
	@echo "PACKAGE_VERSION:                  ${PACKAGE_VERSION}"
	@echo "PYPI_ARTIFACTORY_CREDENTIALS_USR: ${PYPI_ARTIFACTORY_CREDENTIALS_USR} "
	@echo "Make sources:                     ${MAKE_SOURCES}"

install: ## Install setuptools, package, and build utilities
	pip install uv
	uv pip install --upgrade pip setuptools 
	uv pip install -e .[DEV] --extra-index-url ${IHME_PYPI}simple/ --index-strategy unsafe-best-match

install-upstream-deps: # Install upstream dependencies
	@echo "Contents of install_dependency_branch.sh"
	@echo "----------------------------------------"
	@cat $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh
	@echo ""
	@echo "----------------------------------------"
	@sh $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh $(DEPENDENCY_NAME) $(BRANCH_NAME) $(WORKFLOW)

build-env: # Make a new conda environment
	@[ "${CONDA_ENV_NAME}" ] && echo "" > /dev/null || ( echo "CONDA_ENV_NAME is not set"; exit 1 )
	conda create ${CONDA_ENV_CREATION_FLAG} python=${PYTHON_VERSION} --yes

format: setup.py pyproject.toml $(MAKE_SOURCES) # Run the code formatter and import sorter
	isort $(LOCATIONS)
	black $(LOCATIONS)

build-doc: $(MAKE_SOURCES) # Build the Sphinx docs
	$(MAKE) -C docs/ html

build-package: $(MAKE_SOURCES) # Build the package as a pip wheel
	pip install build
	python -m build

clean: # Delete build artifacts and do any custom cleanup such as spinning down services
	@rm -rf format build-doc build-package integration .pytest_cache
	@rm -rf dist output
	$(shell find . -type f -name '*py[co]' -delete -o -type d -name __pycache__ -delete)
