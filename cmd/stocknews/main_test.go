package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"testing"

	"github.com/major/stocknews/internal/config"
)

func TestRun(t *testing.T) {
	oldLoadSettings := loadSettings
	oldNewRuntimeApp := newRuntimeApp
	oldRunApp := runApp
	t.Cleanup(func() {
		loadSettings = oldLoadSettings
		newRuntimeApp = oldNewRuntimeApp
		runApp = oldRunApp
	})
	runApp = run
	loadSettings = func() (config.Settings, error) {
		return config.Settings{}, nil
	}
	runner := &fakeRunner{}
	newRuntimeApp = func(_ config.Settings, _ *http.Client, _ *slog.Logger) appRunner { return runner }
	if err := run(); !errors.Is(err, context.Canceled) {
		t.Fatalf("run() error = %v", err)
	}
}

func TestRunSettingsError(t *testing.T) {
	oldLoadSettings := loadSettings
	oldRunApp := runApp
	t.Cleanup(func() { loadSettings = oldLoadSettings })
	t.Cleanup(func() { runApp = oldRunApp })
	runApp = run
	loadSettings = func() (config.Settings, error) { return config.Settings{}, errors.New("bad env") }
	if err := run(); err == nil || err.Error() != "bad env" {
		t.Fatalf("run() error = %v", err)
	}
}

func TestMainIgnoresContextCanceled(t *testing.T) {
	oldRunApp := runApp
	oldFatalLog := fatalLog
	t.Cleanup(func() {
		runApp = oldRunApp
		fatalLog = oldFatalLog
	})
	runApp = func() error { return context.Canceled }
	called := false
	fatalLog = func(...any) { called = true }
	main()
	if called {
		t.Fatal("fatalLog called for canceled context")
	}
}

func TestMainLogsFatalError(t *testing.T) {
	oldRunApp := runApp
	oldFatalLog := fatalLog
	t.Cleanup(func() {
		runApp = oldRunApp
		fatalLog = oldFatalLog
	})
	runApp = func() error { return errors.New("boom") }
	var got any
	fatalLog = func(args ...any) {
		if len(args) > 0 {
			got = args[0]
		}
	}
	main()
	if err, ok := got.(error); !ok || err.Error() != "boom" {
		t.Fatalf("fatalLog arg = %v", got)
	}
}

type fakeRunner struct{}

func (*fakeRunner) Run(context.Context) error { return context.Canceled }
