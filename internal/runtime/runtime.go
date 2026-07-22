// Package runtime wires the Alpaca stream to Discord delivery.
package runtime

import (
	"context"
	"fmt"
	"html"
	"log/slog"
	"net/http"

	"github.com/major/stocknews/internal/alpaca"
	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/discord"
	"github.com/major/stocknews/internal/earnings"
	"github.com/major/stocknews/internal/news"
)

// App wires the news stream to Discord delivery.
type App struct {
	settings config.Settings
	streamer alpaca.Streamer
	sender   webhookSender
	logger   *slog.Logger
}

type webhookSender interface {
	Send(ctx context.Context, webhookURLs []string, payload *discord.WebhookPayload) error
}

// NewApp builds the application runtime with default integrations.
func NewApp(settings config.Settings, client *http.Client, logger *slog.Logger) *App {
	if logger == nil {
		logger = slog.Default()
	}
	return &App{
		settings: settings,
		streamer: alpaca.NewStreamer(settings),
		sender:   discord.NewSender(client),
		logger:   logger,
	}
}

func newAppWithDeps(settings config.Settings, streamer alpaca.Streamer, sender webhookSender, logger *slog.Logger) *App {
	if logger == nil {
		logger = slog.Default()
	}
	return &App{settings: settings, streamer: streamer, sender: sender, logger: logger}
}

// Run starts the stream, processes incoming items, and stops when the context ends or the stream terminates.
func (a *App) Run(ctx context.Context) error {
	if err := a.streamer.Connect(ctx); err != nil {
		return fmt.Errorf("connect Alpaca news stream: %w", err)
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err, ok := <-a.streamer.Terminated():
			if !ok || err == nil {
				return nil
			}
			return fmt.Errorf("alpaca news stream terminated: %w", err)
		case item, ok := <-a.streamer.Events():
			if !ok {
				return nil
			}
			a.processItem(ctx, item)
		}
	}
}

func (a *App) processItem(ctx context.Context, item news.Item) {
	item.Headline = html.UnescapeString(item.Headline)
	if earnings.HasBlockedPhrases(item.Headline, a.settings.BlockedPhrases) {
		a.logger.Info("skipping news item", "reason", "blocked_phrase", "headline", item.Headline, "author", item.Author, "symbols", item.Symbols)
		return
	}
	if reason, skip := news.Reason(item); skip {
		a.logger.Info("skipping news item", "reason", reason, "headline", item.Headline, "author", item.Author, "symbols", item.Symbols)
		return
	}
	symbol, _ := news.AcceptedSymbol(item)
	kind, _ := news.Classify(item)
	var (
		payload  *discord.WebhookPayload
		built    bool
		webhooks []string
	)
	switch kind {
	case news.KindEarnings:
		payload, built = discord.EarningsPayload(symbol, item.Headline, a.settings.StockLogo, a.settings.TransparentPNG)
		webhooks = a.settings.DiscordEarningsWebhooks
	case news.KindAnalyst:
		payload, built = discord.AnalystPayload(symbol, item.Headline, a.settings.StockLogo, a.settings.TransparentPNG)
		webhooks = a.settings.DiscordAnalystWebhooks
	default:
		payload, built = discord.NewsPayload(item, a.settings.StockLogo, a.settings.TransparentPNG)
		webhooks = a.settings.DiscordNewsWebhooks
	}
	if !built {
		return
	}
	if err := a.sender.Send(ctx, webhooks, payload); err != nil {
		a.logger.Warn("failed to send Discord webhook", "error", err, "kind", kind, "symbol", symbol)
	}
}
