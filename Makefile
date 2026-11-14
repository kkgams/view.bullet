.DEFAULT_GOAL := open
.DELETE_ON_ERROR:
ARCHIVES := build.nosync/view.bullet.zip
NOTICE_FILES := LICENSE NOTICE LICENSING.md
SOURCES := src/view.bullet/views/view-bullet.js
export TMPDIR := $(abspath build.nosync/tmp)
export PYTHONPYCACHEPREFIX := $(abspath build.nosync/pycache)
PYTHON ?= python3
NODE ?= node
CURL ?= curl
GAMS_RELEASE_API ?= https://api.github.com/repos/kkgams/gams/releases/latest
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)
DOCS_PORT ?= 8080
WIKI_VERSION := v1.4.0
WIKI_REPOSITORY := https://github.com/justgook/wiki
WIKI_MAKEFILE := build.nosync/wiki-$(WIKI_VERSION).mk
WIKI_ENGINE := $(abspath build.nosync/wiki-$(WIKI_VERSION))

.PHONY: open check docs serve-docs validate-docs package clean

check:
	@for source in $(SOURCES); do $(NODE) --input-type=module --check < "$$source" || exit; done

# Retain an existing app; never update it implicitly.
tools/gams.app: | build.nosync/tmp
	@test "$(UNAME_S)" = Darwin && test "$(UNAME_M)" = arm64 || \
		{ echo 'GAMS release download requires macOS Apple Silicon' >&2; exit 1; }
	@test ! -e tools/gams-release-notices || \
		{ echo 'tools/gams-release-notices already exists; move it aside before reinstalling GAMS' >&2; exit 1; }
	@mkdir -p tools
	@set -eu; stage=$$(mktemp -d "$(TMPDIR)/gams-download.XXXXXX"); \
		trap 'rm -rf "$$stage"' EXIT; \
		$(CURL) --fail --location --silent --show-error "$(GAMS_RELEASE_API)" --output "$$stage/release.json"; \
		url=$$($(PYTHON) -c 'import json, sys; release = json.load(open(sys.argv[1])); assets = [a for a in release["assets"] if a["name"].startswith("GAMS-") and a["name"].endswith("-macos-aarch64.zip")]; assert len(assets) == 1, "Expected exactly one GAMS macOS Apple Silicon ZIP asset"; print(assets[0]["browser_download_url"])' "$$stage/release.json"); \
		$(CURL) --fail --location --silent --show-error "$$url" --output "$$stage/gams.zip"; \
		unzip -q "$$stage/gams.zip" -d "$$stage/unpacked"; \
		test -f "$$stage/unpacked/GAMS.app/Contents/Info.plist"; \
		test -x "$$stage/unpacked/GAMS.app/Contents/MacOS/gams"; \
		mv "$$stage/unpacked/GAMS.app" "$$stage/GAMS.app"; \
		test ! -e "$$stage/unpacked/.gams-managed"; \
		printf '%s\n' 'view.bullet Makefile Host download' > "$$stage/unpacked/.gams-managed"; \
		mv "$$stage/unpacked" tools/gams-release-notices; \
		mv "$$stage/GAMS.app" "$@"

open: tools/gams.app
	GAMS_APP_CWD="$(CURDIR)" GAMS_CONFIG="gams.json" ./tools/gams.app/Contents/MacOS/gams

build.nosync/tmp:
	@mkdir -p "$@"

$(WIKI_MAKEFILE): | build.nosync/tmp
	@mkdir -p "$(@D)"
	@set -eu; trap 'rm -f "$@.tmp"' EXIT; \
		$(CURL) --fail --location --retry 3 "$(WIKI_REPOSITORY)/raw/$(WIKI_VERSION)/Makefile" -o "$@.tmp"; \
		mv "$@.tmp" "$@"

docs serve-docs validate-docs: $(WIKI_MAKEFILE) | build.nosync/tmp
	$(MAKE) -f "$(WIKI_MAKEFILE)" "$(if $(filter docs,$@),build,$(patsubst %-docs,%,$@))" WIKI_REPOSITORY="$(WIKI_REPOSITORY)" WIKI_VERSION="$(WIKI_VERSION)" WIKI_ENGINE="$(WIKI_ENGINE)" WIKI_SOURCE="$(abspath docs)" WIKI_OUTPUT="$(abspath build.nosync/site)" PORT="$(DOCS_PORT)"

package: build.nosync/SHA256SUMS $(ARCHIVES)

build.nosync/view.bullet.zip: src/view.bullet/views/view-bullet.js $(NOTICE_FILES) Makefile | build.nosync/tmp
	rm -f "$@"
	cd src/view.bullet && zip -q -X "$(CURDIR)/$@" views/view-bullet.js
	zip -q -X "$@" $(NOTICE_FILES)

build.nosync/SHA256SUMS: $(ARCHIVES)
	cd build.nosync && shasum -a 256 $(notdir $(ARCHIVES)) > SHA256SUMS

clean:
	$(PYTHON) -B scripts/clean.py
