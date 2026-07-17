.PHONY: run build

APP := kukicha
CMD := ./cmd/kukicha
BIN := dist/$(APP)
TAGS ?= sqlite_fts5 sqlite_json
GO_TAGS := $(if $(strip $(TAGS)),-tags "$(TAGS)")

run:
	CGO_ENABLED=1 go run $(GO_TAGS) $(CMD)

build:
	mkdir -p dist
	CGO_ENABLED=1 go build $(GO_TAGS) -o $(BIN) $(CMD)
