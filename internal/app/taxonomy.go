package app

import (
	"bytes"
	"crypto/sha256"
	_ "embed"
	"encoding/csv"
	"fmt"
	"slices"
	"strconv"
	"strings"
)

type Kind string

const (
	Genre Kind = "genre"
	Style Kind = "style"
)

type TaxonomyRecord struct {
	Kind       Kind
	Name       string
	Parent     string
	Source     string
	SourceTerm string
	Status     string
	Count      int
}

type Catalog struct {
	records           []TaxonomyRecord
	allValidNormTerms map[string]bool
	Checksum          []byte
}

func (c *Catalog) Records() []TaxonomyRecord {
	return slices.Clone(c.records)
}

func (c *Catalog) IsValidTerm(lookup string) bool {
	return c.allValidNormTerms[strings.ToLower(lookup)]
}

//go:embed data.tsv
var rawData []byte

func LoadTaxonomy() (*Catalog, error) {
	checksum := sha256.Sum256(rawData)

	reader := csv.NewReader(bytes.NewReader(rawData))
	reader.Comma = '\t'
	reader.FieldsPerRecord = 7

	rows, err := reader.ReadAll()
	if err != nil {
		return nil, fmt.Errorf("parse embedded taxonomy: %w", err)
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("embedded taxonomy is empty")
	}

	expectedHeader := []string{
		"kind",
		"name",
		"parent",
		"source",
		"source_term",
		"status",
		"count",
	}
	if err := validateHeader(rows[0], expectedHeader); err != nil {
		return nil, err
	}

	catalog := &Catalog{
		records:           make([]TaxonomyRecord, 0, len(rows)-1),
		allValidNormTerms: make(map[string]bool),
		Checksum:          checksum[:],
	}

	for i, row := range rows {
		if i == 0 {
			continue // skip headers
		}
		if isKnownCorruptRecord(row) {
			continue
		}

		record, err := parseRecord(row)
		if err != nil {
			return nil, fmt.Errorf(
				"parse taxonomy row %d: %w",
				i+1, err,
			)
		}

		catalog.records = append(catalog.records, record)
		catalog.allValidNormTerms[strings.ToLower(record.Name)] = true
		catalog.allValidNormTerms[strings.ToLower(record.SourceTerm)] = true
	}

	return catalog, nil
}

func validateHeader(got, want []string) error {
	if len(got) != len(want) {
		return fmt.Errorf(
			"taxonomy header has %d fields; expected %d",
			len(got),
			len(want),
		)
	}

	for index := range want {
		if got[index] != want[index] {
			return fmt.Errorf(
				"taxonomy header field %d is %q; expected %q",
				index+1,
				got[index],
				want[index],
			)
		}
	}

	return nil
}

func isKnownCorruptRecord(row []string) bool {
	return row[0] == "genre" &&
		row[1] == "1" &&
		row[3] == "discogs_20260301_masters"
}

func parseRecord(row []string) (TaxonomyRecord, error) {
	kind, err := parseKind(row[0])
	if err != nil {
		return TaxonomyRecord{}, err
	}

	name := row[1]
	if name == "" {
		return TaxonomyRecord{}, fmt.Errorf("name is empty")
	}

	parent := row[2]
	if kind == Genre && parent != "" {
		return TaxonomyRecord{}, fmt.Errorf(
			"genre %q unexpectedly has parent %q",
			name, parent,
		)
	}
	if kind == Style && parent == "" {
		return TaxonomyRecord{}, fmt.Errorf(
			"style %q has no parent genre",
			name,
		)
	}

	source := row[3]
	if source == "" {
		return TaxonomyRecord{}, fmt.Errorf("record %q has no source", name)
	}

	sourceTerm := row[4]
	if sourceTerm == "" {
		return TaxonomyRecord{}, fmt.Errorf("record %q has no source term", name)
	}

	status := row[5]
	if status == "" {
		return TaxonomyRecord{}, fmt.Errorf("record %q has no status", name)
	}

	count, err := parseCount(row[6])
	if err != nil {
		return TaxonomyRecord{}, fmt.Errorf(
			"record %q has invalid count: %w",
			name,
			err,
		)
	}

	return TaxonomyRecord{
		Kind:       kind,
		Name:       name,
		Parent:     parent,
		Source:     source,
		SourceTerm: sourceTerm,
		Status:     status,
		Count:      count,
	}, nil
}

func parseKind(value string) (Kind, error) {
	switch value {
	case string(Genre), string(Style):
		return Kind(value), nil
	default:
		return "", fmt.Errorf("unknown taxonomy kind %q", value)
	}
}

func parseCount(value string) (int, error) {
	if value == "" {
		return 0, nil
	}

	return strconv.Atoi(value)
}
