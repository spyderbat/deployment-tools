PYTHON ?= python3
VENV   := .venv
VENV_PY := $(VENV)/bin/python
NAME   := customize-daemonset

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make dev      Create $(VENV) and install dev + runtime deps"
	@echo "  make test     Run the test suite"
	@echo "  make golden   Regenerate golden CLI test outputs"
	@echo "  make build    Build the standalone tarball (dist/$(NAME).tar.gz)"
	@echo "  make dist     Alias for 'make build'"
	@echo "  make clean    Remove build artifacts (dist/, build/, caches)"

# Create the dev virtualenv on first use; re-runs are no-ops once it exists.
.PHONY: dev
dev: $(VENV_PY)
$(VENV_PY):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install -r requirements-dev.txt

.PHONY: test
test: dev
	$(VENV_PY) -m pytest -q

.PHONY: golden
golden: dev
	UPDATE_GOLDEN=1 $(VENV_PY) -m pytest tests/test_cli_golden.py -q

# Build the self-contained bundle. Uses $(PYTHON) (needs pip + network); the
# resulting tarball runs on any compatible python3 with no install.
.PHONY: build
build:
	PYTHON=$(PYTHON) ./build-standalone.sh

.PHONY: dist
dist: build

.PHONY: clean
clean:
	rm -rf dist build customize_daemonset.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
