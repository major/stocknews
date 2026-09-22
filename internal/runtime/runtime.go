// Package runtime wires the Alpaca stream to Discord delivery.
package runtime

import (
	"context"
	"errors"
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

const deliveryQueueCapacity = 16

var errDeliveryQueueFull = errors.New("discord delivery queue is full")

type deliveryJob struct {
	webhookURLs []string
	payload     *discord.WebhookPayload
	kind        news.Kind
	symbol      string
}

type deliveryQueue struct {
	jobs chan deliveryJob
}

func newDeliveryQueue() *deliveryQueue {
	return &deliveryQueue{jobs: make(chan deliveryJob, deliveryQueueCapacity)}
}

func (q *deliveryQueue) enqueue(ctx context.Context, job deliveryJob) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	select {
	case q.jobs <- job:
		return nil
	default:
		return errDeliveryQueueFull
	}
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
func (a *App) Run(ctx context.Context) (runErr error) {
	if err := a.streamer.Connect(ctx); err != nil {
		return fmt.Errorf("connect Alpaca news stream: %w", err)
	}
	queue := newDeliveryQueue()
	workerCtx, cancelWorker := context.WithCancel(ctx)
	workerDone := make(chan struct{})
	go a.runDeliveryWorker(workerCtx, queue, workerDone)
	defer func() {
		if runErr != nil {
			cancelWorker()
		}
		close(queue.jobs)
		<-workerDone
		cancelWorker()
	}()
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
			if err := a.processItem(ctx, item, queue); err != nil {
				return fmt.Errorf("enqueue Discord delivery: %w", err)
			}
		}
	}
}

func (a *App) processItem(ctx context.Context, item news.Item, queue *deliveryQueue) error {
	item.Headline = html.UnescapeString(item.Headline)
	if earnings.HasBlockedPhrases(item.Headline, a.settings.BlockedPhrases) {
		a.logger.Info("skipping news item", "reason", "blocked_phrase", "headline", item.Headline, "author", item.Author, "symbols", item.Symbols)
		return nil
	}
	if reason, skip := news.Reason(item); skip {
		a.logger.Info("skipping news item", "reason", reason, "headline", item.Headline, "author", item.Author, "symbols", item.Symbols)
		return nil
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
		return nil
	}
	return queue.enqueue(ctx, deliveryJob{
		webhookURLs: append([]string(nil), webhooks...),
		payload:     payload,
		kind:        kind,
		symbol:      symbol,
	})
}

func (a *App) runDeliveryWorker(ctx context.Context, queue *deliveryQueue, done chan<- struct{}) {
	defer close(done)
	for {
		select {
		case <-ctx.Done():
			return
		case job, ok := <-queue.jobs:
			if !ok {
				return
			}
			if err := a.sender.Send(ctx, job.webhookURLs, job.payload); err != nil {
				a.logger.Warn("failed to send Discord webhook", "error", err, "kind", job.kind, "symbol", job.symbol)
			}
		}
	}
}
