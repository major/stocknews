// Package main enforces the repository-wide coverage threshold.
package main

import (
	"bufio"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strconv"
	"strings"
)

var (
	statCoverageProfile = os.Stat
	openCoverageProfile = os.Open
)

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		log.Fatal(err)
	}
}

func run(args []string, stdout io.Writer) (err error) {
	fs := flag.NewFlagSet("coveragecheck", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	profilePath := fs.String("profile", "coverage.out", "coverage profile path")
	minimum := fs.Float64("min", 95, "minimum total coverage percent")
	if err := fs.Parse(args); err != nil {
		return err
	}

	if _, err := statCoverageProfile(*profilePath); err != nil {
		return fmt.Errorf("stat coverage profile: %w", err)
	}

	profile, err := openCoverageProfile(*profilePath)
	if err != nil {
		return fmt.Errorf("open coverage profile: %w", err)
	}
	defer func() {
		err = errors.Join(err, profile.Close())
	}()

	percent, err := coveragePercent(profile)
	if err != nil {
		return fmt.Errorf("parse coverage profile: %w", err)
	}
	_, _ = fmt.Fprintf(stdout, "total coverage: %.1f%% (min %.1f%%)\n", percent, *minimum)
	if percent < *minimum {
		return fmt.Errorf("coverage gate failed: %.1f%% < %.1f%%", percent, *minimum)
	}
	return nil
}

func coveragePercent(r io.Reader) (float64, error) {
	scanner := bufio.NewScanner(r)
	lineNumber := 0
	blocks := map[string]coverageBlock{}

	for scanner.Scan() {
		lineNumber++
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if lineNumber == 1 {
			if !strings.HasPrefix(line, "mode:") {
				return 0, fmt.Errorf("line 1: missing mode header")
			}
			continue
		}

		fields := strings.Fields(line)
		if len(fields) != 3 {
			return 0, fmt.Errorf("line %d: expected 3 fields, got %d", lineNumber, len(fields))
		}

		statements, err := strconv.Atoi(fields[1])
		if err != nil {
			return 0, fmt.Errorf("line %d: parse statements: %w", lineNumber, err)
		}
		count, err := strconv.Atoi(fields[2])
		if err != nil {
			return 0, fmt.Errorf("line %d: parse count: %w", lineNumber, err)
		}

		key := fields[0] + " " + fields[1]
		block := blocks[key]
		if block.statements != 0 && block.statements != statements {
			return 0, fmt.Errorf("line %d: conflicting statement count for %q", lineNumber, key)
		}
		block.statements = statements
		block.count += count
		blocks[key] = block
	}
	if err := scanner.Err(); err != nil {
		return 0, err
	}
	if lineNumber == 0 {
		return 0, fmt.Errorf("empty coverage profile")
	}
	totalStatements := 0
	coveredStatements := 0
	for _, block := range blocks {
		totalStatements += block.statements
		if block.count > 0 {
			coveredStatements += block.statements
		}
	}
	if totalStatements == 0 {
		return 0, fmt.Errorf("coverage profile contains no statements")
	}

	return float64(coveredStatements) * 100 / float64(totalStatements), nil
}

type coverageBlock struct {
	statements int
	count      int
}
