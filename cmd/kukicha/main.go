package main

import (
	"fmt"
	"os"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
)

func main() {
	appConfig, err := app.LoadAppConfig()
	if err != nil {
		fail(err)
	}

	if err := scan.Walk(appConfig, walker); err != nil {
		fail(err)
	}
}

func walker(scanFile *scan.ScanFile, err error) error {
	return nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
