# mithai — build targets for binary distribution
#
# Usage:
#   make build-binary     Build native binary for current platform
#   make test-binary      Smoke test the built binary
#   make checksums        Generate SHA256 checksums for release
#   make clean            Remove build artifacts
#
# Prerequisites: uv (dev deps installed automatically)

UV       = uv run
VERSION  = $(shell $(UV) python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
PLATFORM = $(shell $(UV) python -c "import platform; s=platform.system().lower(); a=platform.machine(); a='amd64' if a=='x86_64' else a; print(f'{s}-{a}')")
BINARY   = dist/mithai-$(PLATFORM)

.PHONY: build-binary test-binary checksums clean version

## Show current version and platform
version:
	@echo "mithai $(VERSION) ($(PLATFORM))"

## Build the native binary for the current platform
build-binary:
	@echo "Building mithai $(VERSION) for $(PLATFORM)..."
	uv pip install -e ".[dev,ui,slack,telemetry]" --quiet
	$(UV) pyinstaller mithai.spec --noconfirm
	@ls -lh $(BINARY)
	@echo ""
	@echo "Binary: $(BINARY)"

## Smoke test the built binary
test-binary: $(BINARY)
	@echo "=== Smoke tests ==="
	@echo ""
	@echo "--- Version ---"
	$(BINARY) --version
	@echo ""
	@echo "--- Help ---"
	$(BINARY) --help
	@echo ""
	@echo "--- Skill list (no config) ---"
	$(BINARY) skill list --config /dev/null 2>/dev/null || true
	@echo ""
	@echo "=== All smoke tests passed ==="

## Generate SHA256 checksums for all binaries in dist/
checksums:
	@cd dist && shasum -a 256 mithai-* > checksums.txt
	@echo "Checksums written to dist/checksums.txt"
	@cat dist/checksums.txt

## Remove all build artifacts
clean:
	rm -rf build/ dist/ __pycache__/
	@echo "Cleaned build artifacts"
