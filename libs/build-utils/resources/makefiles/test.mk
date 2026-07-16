define test
	pytest -vvv -n auto $(if $(RUNSLOW),--runslow,) $(if $(RUNWEEKLY),--runweekly,) tests/$(1)
endef

test-all: # Run all tests
	pytest -vvv -n auto $(if $(RUNSLOW),--runslow,) $(if $(RUNWEEKLY),--runweekly,) tests/

test-e2e: # Run end-to-end tests
	$(call test,e2e)

test-integration: # Run integration tests
	$(call test,integration)

test-unit: # Run unit tests
	$(call test,unit)
