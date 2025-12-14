UV ?= uv
PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin

.DEFAULT_GOAL := help

.PHONY: help install install-dev validate test e2e build release-build bundle install-bundle standalone clean

help:
	@echo "Sitesync Make targets:"
	@echo "  install       Build a wheel and install sitesync into $(BINDIR)"
	@echo "  install-dev   Install dev dependencies into the uv environment"
	@echo "  validate      Run all quality checks (ruff, uv ty, pytest)"
	@echo "  test          Run unit tests"
	@echo "  e2e           Run end-to-end tests"
	@echo "  build         Build a wheel"
	@echo "  standalone    Build a bundled executable and copy it to $(BINDIR)"
	@echo "  clean         Remove build artifacts"
	@echo ""
	@echo "Overrides:"
	@echo "  PREFIX=/opt   Install prefix used by make install (bin lives in PREFIX/bin)"
	@echo "  BINDIR=/opt/bin   Install directory for standalone binary"

install: build
	install -d $(BINDIR)
	$(UV) pip install --prefix $(PREFIX) --force-reinstall dist/sitesync-*.whl

install-dev:
	$(UV) sync --extra dev

validate:
	$(UV) run ruff check --fix src tests
	$(UV) run ruff format src tests
	uvx ty check src
	$(UV) run pytest tests --ignore=tests/e2e

test:
	$(UV) run pytest tests --ignore=tests/e2e

e2e:
	$(UV) run pytest tests/e2e -s -vv

build:
	rm -f dist/sitesync-*.whl
	$(UV) build --wheel

release-build:
	$(UV) build
	@echo "Wheel built in dist/"

bundle:
	$(UV) run --extra bundle pyinstaller --clean --noconfirm \
		--distpath build/pyinstaller/dist \
		--workpath build/pyinstaller/work \
		sitesync.spec

install-bundle: bundle
	install -d $(BINDIR)
	install -m 0755 build/pyinstaller/dist/sitesync $(BINDIR)/sitesync

standalone: install-bundle

clean:
	rm -rf build dist .ruff_cache .mypy_cache __pycache__ */__pycache__
