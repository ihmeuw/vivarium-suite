SHELL := /bin/bash
UTILS_DIR := $(dir $(abspath $(dir $(abspath $(dir $(lastword $(MAKEFILE_LIST)))))))
.DEFAULT_GOAL := list # If someone runs "make", run "make list"

# Source files to format, lint, and type check.
LOCATIONS=src tests

# Unless overridden, build conda environment using the package name.
SAFE_NAME = $(shell python -c "from pkg_resources import safe_name; print(safe_name(\"$(PACKAGE_NAME)\"))")

PACKAGE_VERSION = $(shell grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' CHANGELOG.rst | head -n 1 | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+')

# Use this URL to pull IHME Python packages and deploy this package to PyPi.
IHME_PYPI := https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/

# If CONDA_ENV_PATH is set (from a Jenkins build), use the -p flag when making Conda env in
# order to make env at specific path. Otherwise, make a named env at the default path using
# the -n flag.
PYTHON_VERSION ?= 3.11
CONDA_ENV_NAME ?= ${PACKAGE_NAME}_py${PYTHON_VERSION}
CONDA_ENV_CREATION_FLAG = $(if $(CONDA_ENV_PATH),-p ${CONDA_ENV_PATH},-n ${CONDA_ENV_NAME})

.PHONY: help
help: # Curated help message 
	(echo; \
	echo "For Make's standard help, run 'make --help'."; \
	echo; \
	echo "=================="; \
	echo "Diagnostic targets"; \
	echo "=================="; \
	echo; \
	echo "list       Print available Make targets"; \
	echo "debug      Print debug information"; \
	echo; \
	echo "=============="; \
	echo "Helper targets"; \
	echo "=============="; \
	echo; \
	echo "build-env                    Create a new environment with installed packages"; \
	echo "check                        Run development checks"; \
	echo "                             (isort, black, mypy, tests, docs)"; \
	echo "install-upstream-deps        Install upstream dependencies"; \
	echo "format                       Format code (isort and black)"; \
	echo "manual-deploy-artifactory    Deploy package; only use if Jenkins deploy fails"; \
	echo; \
	echo "====================="; \
	echo "Jenkins build targets"; \
	echo "====================="; \
	echo; \
	echo "Run the following targets (in order) to mimic a Jenkins build:"; \
	echo "  1. create-env                       Create a new conda environment"; \
	echo "  2. install                          Install package and dependencies"; \
	echo "  3. lint                             Check for formatting errors"; \
	echo "  4. mypy                             (optional) Check for type hinting errors"; \
	echo "  5. test-<test-type>                 Run specific tests unit, integration,"; \
	echo "                                      e2e, all); include RUNSLOW=true if desired"; \
	echo "  6. build-docs                       Build documentation"; \
	echo "  7. test-docs                        Test documentation examples"; \
	echo "  8. Deploy the package (optional)"; \
	echo "     a. tag-version                   Tag current version and push to git"; \
	echo "     b. build-package                 Build pip wheel package"; \
	echo "     c. deploy-package-artifactory    Deploy the package to Artifactory"; \
	echo "  9. deploy-docs                      Deploy documentation to shared server"; \
	echo " 10. clean                            Clean build artifacts and temporary files"; \
	echo) | less

######################
# Diagnostic targets #
######################

# List of Make targets is generated dynamically. Note that to have a target show up in this
# list, it must have an in-line comment starting with a '#' on the target definition,
# e.g. some-target:  # this is the description for some-target
.PHONY: list
list: # Print available Make targets
	@echo
	@echo "Make targets:"
	@for file in Makefile $(UTILS_DIR)resources/makefiles/*.mk; do \
		grep -i "^[a-zA-Z][a-zA-Z0-9_ \.\-]*: .*[#].*" $$file | sort | sed 's/:.*#/ : /g'; \
	done | column -t -s:
	@echo

.PHONY: debug
debug: # Print debug information
	@echo
	@echo "'make' invoked with these environment variables:"
	@echo "CONDA_ENV_NAME:                   ${CONDA_ENV_NAME}"
	@echo "PYTHON_VERSION:                   ${PYTHON_VERSION}"
	@echo "IHME_PYPI:                        ${IHME_PYPI}"
	@echo "LOCATIONS:                        ${LOCATIONS}"
	@echo "PACKAGE_NAME:                     ${PACKAGE_NAME}"
	@echo "PACKAGE_VERSION:                  ${PACKAGE_VERSION}"
	@echo "PYPI_ARTIFACTORY_CREDENTIALS_USR: ${PYPI_ARTIFACTORY_CREDENTIALS_USR} "
	@echo
	@echo "vivarium_build_utils version:     $(shell python -c "import importlib.metadata; print(importlib.metadata.version('vivarium_build_utils'))" 2>/dev/null || echo "unknown")"
	@echo

#########################
# Jenkins build targets #
#########################

.PHONY: create-env
create-env: # Create a new conda environment
# env name: {PACKAGE_NAME}_py{PYTHON_VERSION}.
	conda create ${CONDA_ENV_CREATION_FLAG} python=${PYTHON_VERSION} --yes
	@echo
	@echo "Environment created ($(CONDA_ENV_NAME))"
	@echo

.PHONY: install
install: ENV_REQS?=dev
install: UV_FLAGS?=
install: # Install package and dependencies
	pip install uv
	uv pip install --upgrade pip setuptools 
	uv pip install -e .[${ENV_REQS}] --extra-index-url ${IHME_PYPI}simple/ --index-strategy unsafe-best-match ${UV_FLAGS}

.PHONY: lint
lint: # Check for formatting errors
# NOTE: This is not actually running isort and black but rather just checking the --diffs.
	@echo
	@echo "Running isort and black"
	isort $(LOCATIONS) --check --verbose --only-modified --diff
	black $(LOCATIONS) --check --diff

.PHONY: mypy
mypy: # Check for type hinting errors
	mypy --config-file pyproject.toml .

# test: test targets are defined in test.mk

.PHONY: build-docs
build-docs: # Build documentation
	@if [ -d docs/ ]; then \
		rm -rf docs/build/; \
		$(MAKE) -C docs/ html SPHINXOPTS="-T -W --keep-going"; \
	else \
		echo "No 'docs/' folder found - skipping."; \
	fi

.PHONY: test-docs
test-docs: # Test documentation examples
	@if [ -d docs/ ]; then \
		$(MAKE) doctest -C docs/; \
	else \
		echo "No 'docs/' folder found - skipping."; \
	fi

.PHONY: tag-version
tag-version: # Tag current version and push to git
	git tag -a "v${PACKAGE_VERSION}" -m "Tag automatically generated from Jenkins."
	git push --tags

.PHONY: build-package
build-package: # Build pip wheel package
	pip install build
	python -m build

.PHONY: deploy-package-artifactory
deploy-package-artifactory: # Deploy the package to Artifactory
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_USR}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_USR is not set, export using simsci artifactory credentials"; exit 1 )
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_PSW}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_PSW is not set, export using simsci artifactory credentials"; exit 1 )
	pip install twine
	twine upload --repository-url ${IHME_PYPI} -u ${PYPI_ARTIFACTORY_CREDENTIALS_USR} -p ${PYPI_ARTIFACTORY_CREDENTIALS_PSW} dist/*

.PHONY: deploy-docs
deploy-docs: # Deploy documentation to shared server
	@[ "${DOCS_ROOT_PATH}" ] && echo "" > /dev/null || ( echo "DOCS_ROOT_PATH is not set"; exit 1 )
	mkdir -m 0775 -p ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	cp -R ./docs/build/html/* ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	chmod -R 0775 ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	cd ${DOCS_ROOT_PATH}/${PACKAGE_NAME} && ln -nsFfv ${PACKAGE_VERSION} current

.PHONY: clean
clean: # Clean build artifacts and temporary files
	@rm -rf format build-docs build-package integration .pytest_cache
	@rm -rf dist output
	$(shell find . -type f -name '*py[co]' -delete -o -type d -name __pycache__ -delete)

##################
# Helper targets #
##################

.PHONY: check
check: # Run development checks
	make lint
# 	Run mypy if the py.typed file exists
	@if [ -f "src/$(PACKAGE_NAME)/py.typed" ]; then \
		echo; \
		echo "Running mypy"; \
		make mypy; \
	fi
# 	Run all fast tests
	@echo
	@echo "Running fast tests"
	make test-all
# 	Build docs
	@echo
	@echo "Building documentation"
	make build-docs
# 	Test docs
	@echo
	@echo "Running doctests"
	make test-docs
	@echo
	@echo "*** All checks passed successfully! ***"

.PHONY: install-upstream-deps
install-upstream-deps: # Install upstream dependencies
	@echo "Contents of install_dependency_branch.sh"
	@echo "----------------------------------------"
	@cat $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh
	@echo
	@echo "----------------------------------------"
	@sh $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh $(DEPENDENCY_NAME) $(BRANCH_NAME) $(WORKFLOW)
	@echo

.PHONY: format
format: # Format code (isort and black)
	isort $(LOCATIONS)
	black $(LOCATIONS)

.PHONY: manual-deploy-artifactory
manual-deploy-artifactory: # Deploy package; only use if Jenkins deploy fails
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_USR}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_USR is not set, export using simsci artifactory credentials"; exit 1 )
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_PSW}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_PSW is not set, export using simsci artifactory credentials"; exit 1 )
	make build-package
	make tag-version
	make deploy-package-artifactory
