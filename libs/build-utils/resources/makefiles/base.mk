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

# These are the doc and source code files in this repo.
# When one of these files changes, it means that Make targets need to run again.
MAKE_SOURCES := $(shell find . -type d -name "*" ! -path "./.git*" ! -path "./.vscode" ! -path "./output" ! -path "./output/*" ! -path "./archive" ! -path "./dist" ! -path "./output/htmlcov*" ! -path "**/.pytest_cache*" ! -path "**/__pycache__" ! -path "./output/docs_build*" ! -path "./.pytype*" ! -path "." ! -path "./src/${PACKAGE_NAME}/legacy*" ! -path ./.history ! -path "./.history/*" ! -path "./src/${PACKAGE_NAME}.egg-info" ! -path ./.idea ! -path "./.idea/*" )

# Phony targets don't produce artifacts.
.PHONY: .list-targets build-env build-doc format lint mypy integration build-package clean debug deploy-doc deploy-package full help list quick install install-upstream-deps

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
	
install: ENV_REQS?=dev
install: ## Install setuptools, package, and build utilities
	pip install uv
	uv pip install --upgrade pip setuptools 
	uv pip install -e .[${ENV_REQS}] --extra-index-url ${IHME_PYPI}simple/ --index-strategy unsafe-best-match

install-upstream-deps: # Install upstream dependencies
	@echo "Contents of install_dependency_branch.sh"
	@echo "----------------------------------------"
	@cat $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh
	@echo ""
	@echo "----------------------------------------"
	@sh $(UTILS_DIR)/resources/scripts/install_dependency_branch.sh $(DEPENDENCY_NAME) $(BRANCH_NAME) $(WORKFLOW)

create-env: ## Create a new conda environment. Specify env name with CONDA_ENV_NAME. Default env name is PACKAGE_NAME_PYTHON_VERSION.
	conda create ${CONDA_ENV_CREATION_FLAG} python=${PYTHON_VERSION} --yes

build-env: ## Create environment and install packages. Specify env name with CONDA_ENV_NAME. Default env name is PACKAGE_NAME_PYTHON_VERSION.
	make create-env CONDA_ENV_NAME=$(CONDA_ENV_NAME)
	conda run -n $(CONDA_ENV_NAME) make install
	
format: setup.py pyproject.toml $(MAKE_SOURCES) # Run the code formatter and import sorter
	isort $(LOCATIONS)
	black $(LOCATIONS)
	@echo "Ignore, Created by Makefile, `date`" > $@

lint: # Check for formatting errors
	isort $(LOCATIONS) --check --verbose --only-modified --diff
	black $(LOCATIONS) --check --diff

mypy: # Check for type hinting erros
	mypy --config-file pyproject.toml .

test-doc: $(MAKE_SOURCES) # Test docs
	$(MAKE) doctest -C docs/

build-doc: $(MAKE_SOURCES) # Build the docs
	$(MAKE) -C docs/ html SPHINXOPTS="-T -W --keep-going"
	@echo "Ignore, Created by Makefile, `date`" > $@

deploy-doc: # Deploy the Sphinx docs
	@[ "${DOCS_ROOT_PATH}" ] && echo "" > /dev/null || ( echo "DOCS_ROOT_PATH is not set"; exit 1 )
	mkdir -m 0775 -p ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	cp -R ./output/docs_build/* ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	chmod -R 0775 ${DOCS_ROOT_PATH}/${PACKAGE_NAME}/${PACKAGE_VERSION}
	cd ${DOCS_ROOT_PATH}/${PACKAGE_NAME} && ln -nsFfv ${PACKAGE_VERSION} current

build-package: $(MAKE_SOURCES) # Build the package as a pip wheel
	pip install build
	python -m build
	@echo "Ignore, Created by Makefile, `date`" > $@

deploy-package-artifactory: # Deploy the package to Artifactory
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_USR}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_USR is not set, export using simsci artifactory credentials"; exit 1 )
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_PSW}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_PSW is not set, export using simsci artifactory credentials"; exit 1 )
	pip install twine
	twine upload --repository-url ${IHME_PYPI} -u ${PYPI_ARTIFACTORY_CREDENTIALS_USR} -p ${PYPI_ARTIFACTORY_CREDENTIALS_PSW} dist/*

tag-version: # Tag the version and push
	git tag -a "v${PACKAGE_VERSION}" -m "Tag automatically generated from Jenkins."
	git push --tags

manual-deploy: # Manual deploy to Artifactory
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_USR}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_USR is not set, export using simsci artifactory credentials"; exit 1 )
	@[ "${PYPI_ARTIFACTORY_CREDENTIALS_PSW}" ] && echo "" > /dev/null || ( echo "PYPI_ARTIFACTORY_CREDENTIALS_PSW is not set, export using simsci artifactory credentials"; exit 1 )
	make build-env
	conda run -n $(CONDA_ENV_NAME) make build-package
	conda run -n $(CONDA_ENV_NAME) make tag-version
	conda run -n $(CONDA_ENV_NAME) make deploy-package-artifactory

clean: # Delete build artifacts and do any custom cleanup such as spinning down services
	@rm -rf format build-doc build-package integration .pytest_cache
	@rm -rf dist output
	$(shell find . -type f -name '*py[co]' -delete -o -type d -name __pycache__ -delete)
