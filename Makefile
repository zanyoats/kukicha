.PHONY: run build

APP := kukicha
CMD := ./cmd/kukicha
BIN := dist/$(APP)
TAGS ?= sqlite_fts5 sqlite_json
GO_TAGS := $(if $(strip $(TAGS)),-tags "$(TAGS)")

run: build
	./$(BIN)

build:
	mkdir -p dist
	CGO_ENABLED=1 go build $(GO_TAGS) -o $(BIN) $(CMD)
