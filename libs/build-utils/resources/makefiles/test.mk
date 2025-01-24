define test
	export COVERAGE_FILE=./output/.coverage.$(1) && \
	pytest -vvv $(if $(RUNSLOW),--runslow,) --cov --cov-report term --cov-report html:./output/htmlcov_$(1) tests/$(1)
endef

all-tests: $(MAKE_SOURCES) # Run all tests
	export COVERAGE_FILE=./output/.coverage && \
	pytest -vvv $(if $(RUNSLOW),--runslow,) --cov --cov-report term --cov-report html:./output/htmlcov_tests tests/

e2e: $(MAKE_SOURCES) # Run end-to-end tests
	$(call test,e2e)

integration: $(MAKE_SOURCES) # Run integration tests
	$(call test,integration)

unit: $(MAKE_SOURCES) # Run unit tests
	$(call test,unit)