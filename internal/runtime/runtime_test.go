package runtime

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/discord"
	"github.com/major/stocknews/internal/news"
)

func TestAppRunRoutesPayloads(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	sender := &fakeSender{}
	app := newAppWithDeps(testSettings(), streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(ctx) }()
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "AAPL Q1 EPS $2.00 vs $1.80 est."}
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Baird Upgrades Apple to Outperform, Raises Price Target to $200"}
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple &amp; launches new iPhone", Summary: "summary", URL: "https://example.test"}
	streamer.events <- news.Item{Symbols: []string{"AAPL", "MSFT"}, Author: "Benzinga Newsdesk", Headline: "ignored"}
	close(streamer.events)
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(sender.calls) != 3 {
		t.Fatalf("calls = %d, want 3", len(sender.calls))
	}
	if got := sender.calls[0].webhooks[0]; got != "earnings-hook" {
		t.Fatalf("earnings webhook = %q", got)
	}
	if got := sender.calls[1].webhooks[0]; got != "analyst-hook" {
		t.Fatalf("analyst webhook = %q", got)
	}
	if got := sender.calls[2].payload.Embeds[0].Title; got != "AAPL: Apple & launches new iPhone" {
		t.Fatalf("news title = %q", got)
	}
}

func TestAppRunSkipsBlockedHeadlines(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	sender := &fakeSender{}
	settings := testSettings()
	settings.BlockedPhrases = []string{"would be worth"}
	app := newAppWithDeps(settings, streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)))
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(context.Background()) }()
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple would be worth $1M if you invested early"}
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple launches new iPhone"}
	close(streamer.events)
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(sender.calls) != 1 {
		t.Fatalf("calls = %d, want 1", len(sender.calls))
	}
	if got := sender.calls[0].payload.Embeds[0].Title; got != "AAPL: Apple launches new iPhone" {
		t.Fatalf("title = %q", got)
	}
}

func TestNewApp(t *testing.T) {
	t.Parallel()
	app := NewApp(testSettings(), nil, nil)
	if app == nil || app.sender == nil || app.streamer == nil || app.logger == nil {
		t.Fatal("expected app dependencies")
	}
}

func TestNewAppWithDepsDefaultsLogger(t *testing.T) {
	t.Parallel()
	app := newAppWithDeps(testSettings(), newFakeStreamer(), &fakeSender{}, nil)
	if app.logger == nil {
		t.Fatal("expected logger")
	}
}

func TestAppRunReturnsTerminationError(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	app := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	streamer.terminated <- errors.New("boom")
	err := app.Run(context.Background())
	if err == nil || err.Error() != "alpaca news stream terminated: boom" {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsNilWhenTerminationChannelCloses(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	close(streamer.terminated)
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil))).Run(context.Background())
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsNilWhenTerminationReportsNil(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	streamer.terminated <- nil
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil))).Run(context.Background())
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsConnectError(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	streamer.connectErr = errors.New("nope")
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil))).Run(context.Background())
	if err == nil || err.Error() != "connect Alpaca news stream: nope" {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunStopsOnContext(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil))).Run(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunLogsSendErrorsAndContinues(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	sender := &fakeSender{err: errors.New("discord down")}
	app := newAppWithDeps(testSettings(), streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)))
	errCh := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	go func() { errCh <- app.Run(ctx) }()
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple releases new iPhone"}
	streamer.events <- news.Item{Symbols: []string{"MSFT"}, Author: "Benzinga Newsdesk", Headline: "Microsoft unveils new Surface device"}
	close(streamer.events)
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(sender.calls) != 2 {
		t.Fatalf("calls = %d, want 2", len(sender.calls))
	}
}

func TestAppRunSkipsEmptyBuiltPayload(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	sender := &fakeSender{}
	app := newAppWithDeps(testSettings(), streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)))
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(context.Background()) }()
	streamer.events <- news.Item{Symbols: []string{"NVDA"}, Author: "Benzinga Newsdesk", Headline: "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850"}
	close(streamer.events)
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if len(sender.calls) != 0 {
		t.Fatalf("calls = %d, want 0", len(sender.calls))
	}
}

type fakeStreamer struct {
	events     chan news.Item
	terminated chan error
	connectErr error
}

func newFakeStreamer() *fakeStreamer {
	return &fakeStreamer{events: make(chan news.Item, 8), terminated: make(chan error, 1)}
}

func (f *fakeStreamer) Connect(context.Context) error { return f.connectErr }
func (f *fakeStreamer) Events() <-chan news.Item      { return f.events }
func (f *fakeStreamer) Terminated() <-chan error      { return f.terminated }

type fakeSender struct {
	calls []sendCall
	err   error
}

type sendCall struct {
	webhooks []string
	payload  *discord.WebhookPayload
}

func (f *fakeSender) Send(_ context.Context, webhookURLs []string, payload *discord.WebhookPayload) error {
	copyHooks := append([]string(nil), webhookURLs...)
	f.calls = append(f.calls, sendCall{webhooks: copyHooks, payload: payload})
	return f.err
}

func testSettings() config.Settings {
	return config.Settings{
		DiscordAnalystWebhooks:  []string{"analyst-hook"},
		DiscordEarningsWebhooks: []string{"earnings-hook"},
		DiscordNewsWebhooks:     []string{"news-hook"},
		StockLogo:               "https://static.stocktitan.net/company-logo/%s.webp",
		TransparentPNG:          "https://major.io/transparent.png",
		BlockedPhrases:          []string{"if you invested", "would be worth"},
	}
}
