.PHONY: run dev build

APP := kukicha
CMD := ./cmd/kukicha
BIN := dist/$(APP)
TAGS ?= sqlite_fts5 sqlite_json
GO_TAGS := $(if $(strip $(TAGS)),-tags "$(TAGS)")
AIR_VERSION := v1.67.4
AIR_BIN := $(CURDIR)/dist/tools/$(AIR_VERSION)/air

# Development defaults; environment or command-line values take precedence.
run dev: export KUKICHA_JWT_SECRET ?= QXxptYUafckLz8ltSkpMAGpteBLmCFwQAa07/6ZBljk=
run dev: export KUKICHA_PASSPHRASE ?= topsecret
run: build
	./$(BIN)

# Install the pinned watcher locally on first use; no global Air install needed.
dev: $(AIR_BIN)
	"$(AIR_BIN)" -c .air.toml

$(AIR_BIN):
	mkdir -p "$(@D)"
	GOBIN="$(@D)" go install github.com/air-verse/air@$(AIR_VERSION)

build:
	mkdir -p dist
	CGO_ENABLED=1 go build $(GO_TAGS) -o $(BIN) $(CMD)
