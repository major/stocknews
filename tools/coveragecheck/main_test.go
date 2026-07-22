package main

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCoveragePercentUsesCounters(t *testing.T) {
	t.Parallel()

	profile := strings.NewReader("mode: count\nexample.go:1.1,2.1 99 1\nexample.go:3.1,4.1 1 0\n")

	percent, err := coveragePercent(profile)
	if err != nil {
		t.Fatalf("coveragePercent() error = %v", err)
	}

	if percent != 99 {
		t.Fatalf("coveragePercent() = %v, want 99", percent)
	}
}

func TestCoveragePercentMergesDuplicateBlocks(t *testing.T) {
	t.Parallel()

	profile := strings.NewReader("mode: count\nexample.go:1.1,2.1 2 0\nexample.go:1.1,2.1 2 3\nexample.go:3.1,4.1 1 0\n")

	percent, err := coveragePercent(profile)
	if err != nil {
		t.Fatalf("coveragePercent() error = %v", err)
	}

	if percent != float64(2)*100/3 {
		t.Fatalf("coveragePercent() = %v, want %v", percent, float64(2)*100/3)
	}
}

func TestCoveragePercentRejectsInvalidProfile(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(strings.NewReader("bad\n"))
	if err == nil {
		t.Fatal("coveragePercent() error = nil, want error")
	}
}

func TestCoveragePercentRejectsProfilesWithoutStatements(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(strings.NewReader("mode: count\n"))
	if err == nil {
		t.Fatal("coveragePercent() error = nil, want error")
	}
}

func TestCoveragePercentPropagatesScannerErrors(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(errorReader{})
	if !errors.Is(err, errReaderFailed) {
		t.Fatalf("coveragePercent() error = %v, want %v", err, errReaderFailed)
	}
}

func TestCoveragePercentRejectsMalformedFields(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(strings.NewReader("mode: count\nexample.go:1.1,2.1 2\n"))
	if err == nil {
		t.Fatal("coveragePercent() error = nil, want error")
	}
}

func TestCoveragePercentRejectsInvalidStatements(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(strings.NewReader("mode: count\nexample.go:1.1,2.1 nope 1\n"))
	if err == nil {
		t.Fatal("coveragePercent() error = nil, want error")
	}
}

func TestCoveragePercentRejectsInvalidCount(t *testing.T) {
	t.Parallel()

	_, err := coveragePercent(strings.NewReader("mode: count\nexample.go:1.1,2.1 2 nope\n"))
	if err == nil {
		t.Fatal("coveragePercent() error = nil, want error")
	}
}

func TestRun(t *testing.T) {
	t.Parallel()

	profilePath := filepath.Join(t.TempDir(), "coverage.out")
	if err := os.WriteFile(profilePath, []byte("mode: count\nexample.go:1.1,2.1 1 1\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	var stdout bytes.Buffer
	err := run([]string{"-profile", profilePath, "-min", "95"}, &stdout)
	if err != nil {
		t.Fatalf("run() error = %v", err)
	}
	if got := stdout.String(); got != "total coverage: 100.0% (min 95.0%)\n" {
		t.Fatalf("stdout = %q", got)
	}
}

func TestRunReturnsGateFailure(t *testing.T) {
	t.Parallel()

	profilePath := filepath.Join(t.TempDir(), "coverage.out")
	if err := os.WriteFile(profilePath, []byte("mode: count\nexample.go:1.1,2.1 1 0\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	err := run([]string{"-profile", profilePath, "-min", "95"}, io.Discard)
	if err == nil || err.Error() != "coverage gate failed: 0.0% < 95.0%" {
		t.Fatalf("run() error = %v", err)
	}
}

func TestRunReturnsStatError(t *testing.T) {
	t.Parallel()

	err := run([]string{"-profile", filepath.Join(t.TempDir(), "missing.out")}, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "stat coverage profile") {
		t.Fatalf("run() error = %v", err)
	}
}

func TestRunReturnsOpenError(t *testing.T) {
	oldStat := statCoverageProfile
	oldOpen := openCoverageProfile
	t.Cleanup(func() {
		statCoverageProfile = oldStat
		openCoverageProfile = oldOpen
	})
	statCoverageProfile = func(string) (os.FileInfo, error) { return fakeFileInfo{}, nil }
	openCoverageProfile = func(string) (*os.File, error) { return nil, errReaderFailed }

	err := run(nil, io.Discard)
	if !errors.Is(err, errReaderFailed) || !strings.Contains(err.Error(), "open coverage profile") {
		t.Fatalf("run() error = %v", err)
	}
}

func TestRunReturnsParseError(t *testing.T) {
	t.Parallel()

	profilePath := filepath.Join(t.TempDir(), "coverage.out")
	if err := os.WriteFile(profilePath, []byte("bad\n"), 0o600); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	err := run([]string{"-profile", profilePath}, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "parse coverage profile") {
		t.Fatalf("run() error = %v", err)
	}
}

type fakeFileInfo struct{}

func (fakeFileInfo) Name() string       { return "coverage.out" }
func (fakeFileInfo) Size() int64        { return 0 }
func (fakeFileInfo) Mode() os.FileMode  { return 0 }
func (fakeFileInfo) ModTime() time.Time { return time.Time{} }
func (fakeFileInfo) IsDir() bool        { return false }
func (fakeFileInfo) Sys() any           { return nil }

type errorReader struct{}

var errReaderFailed = errors.New("reader failed")

func (errorReader) Read(_ []byte) (int, error) {
	return 0, errReaderFailed
}
