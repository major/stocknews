// Package main starts the stocknews process.
package main

import (
	"context"
	"errors"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/runtime"
)

var (
	version       = "dev"
	commit        = "unknown"
	buildDate     = "unknown"
	runApp        = run
	fatalLog      = log.Fatal
	loadSettings  = config.FromEnv
	newRuntimeApp = func(settings config.Settings, client *http.Client, logger *slog.Logger) appRunner {
		return runtime.NewApp(settings, client, logger)
	}
)

type appRunner interface {
	Run(ctx context.Context) error
}

func main() {
	if err := runApp(); err != nil && !errors.Is(err, context.Canceled) {
		fatalLog(err)
	}
}

func run() error {
	settings, err := loadSettings()
	if err != nil {
		return err
	}
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	logger.Info("starting stocknews", "version", version, "commit", commit, "build_date", buildDate)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	client := &http.Client{Timeout: 10 * time.Second}
	return newRuntimeApp(settings, client, logger).Run(ctx)
}
