package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/major/stocknews/internal/alpaca"
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

func TestAppRunReturnsStockConnectError(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	trades.connectErr = errors.New("no stock stream")
	writer := &signalWriter{writes: make(chan struct{}, 1)}
	app := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewJSONHandler(writer, nil)), trades)
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(context.Background()) }()
	select {
	case <-writer.writes:
		close(streamer.events)
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for stock connection log")
	}
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	var record map[string]any
	if err := json.Unmarshal(writer.output.Bytes(), &record); err != nil {
		t.Fatalf("log JSON error = %v", err)
	}
	if record["msg"] != "failed to connect Alpaca stock stream" || record["error"] != "no stock stream" {
		t.Fatalf("log record = %#v", record)
	}
}

func TestAppRunProcessesNewsWhileStockConnects(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	trades.connectStarted = make(chan struct{})
	trades.connectDone = make(chan struct{})
	trades.blockConnect = true
	sender := &fakeSender{}
	app := newAppWithDeps(testSettings(), streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)), trades)
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(context.Background()) }()
	select {
	case <-trades.connectStarted:
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for stock connection")
	}
	streamer.events <- news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple releases new iPhone"}
	close(streamer.events)
	if err := <-errCh; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	select {
	case <-trades.connectDone:
	case <-time.After(time.Second):
		t.Fatal("stock connection did not stop")
	}
	if len(sender.calls) != 1 {
		t.Fatalf("calls = %d, want 1", len(sender.calls))
	}
}

func TestAppRunReturnsStockTerminationError(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	trades.terminated <- errors.New("stock stream stopped")
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil)), trades).Run(context.Background())
	if err == nil || err.Error() != "alpaca stock stream terminated: stock stream stopped" {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsNilWhenStockTerminationChannelCloses(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	close(trades.terminated)
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil)), trades).Run(context.Background())
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsNilWhenStockTerminationReportsNil(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	trades.terminated <- nil
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil)), trades).Run(context.Background())
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppRunReturnsNilWhenStockTradeChannelCloses(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	close(trades.trades)
	err := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewTextHandler(io.Discard, nil)), trades).Run(context.Background())
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
}

func TestAppLogsStructuredTrade(t *testing.T) {
	t.Parallel()
	var output bytes.Buffer
	app := newAppWithDeps(testSettings(), newFakeStreamer(), &fakeSender{}, slog.New(slog.NewJSONHandler(&output, nil)))
	trade := alpaca.Trade{
		Symbol:     "SPY",
		Price:      500.25,
		Size:       100,
		Exchange:   "V",
		Timestamp:  time.Date(2026, time.September, 21, 14, 30, 0, 0, time.UTC),
		Conditions: []string{"@", "F"},
		Tape:       "C",
	}
	app.logTrade(trade)
	var record map[string]any
	if err := json.Unmarshal(output.Bytes(), &record); err != nil {
		t.Fatalf("log JSON error = %v", err)
	}
	if record["msg"] != "stock trade" || record["symbol"] != "SPY" || record["price"] != 500.25 || record["size"] != float64(100) || record["exchange"] != "V" || record["tape"] != "C" {
		t.Fatalf("log record = %#v", record)
	}
	if record["timestamp"] != trade.Timestamp.Format(time.RFC3339Nano) {
		t.Fatalf("timestamp = %v", record["timestamp"])
	}
	conditions, ok := record["conditions"].([]any)
	if !ok || len(conditions) != 2 || conditions[0] != "@" || conditions[1] != "F" {
		t.Fatalf("conditions = %#v", record["conditions"])
	}
}

func TestAppRunLogsTradeEvents(t *testing.T) {
	t.Parallel()
	streamer := newFakeStreamer()
	trades := newFakeTradeStreamer()
	writer := &signalWriter{writes: make(chan struct{}, 1)}
	app := newAppWithDeps(testSettings(), streamer, &fakeSender{}, slog.New(slog.NewJSONHandler(writer, nil)), trades)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	errCh := make(chan error, 1)
	go func() { errCh <- app.Run(ctx) }()
	trades.trades <- alpaca.Trade{Symbol: "QQQ", Price: 400, Size: 1}
	select {
	case <-writer.writes:
		cancel()
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for trade log")
	}
	if err := <-errCh; !errors.Is(err, context.Canceled) {
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

func TestDeliveryQueueReturnsErrorWhenFull(t *testing.T) {
	t.Parallel()
	queue := newDeliveryQueue()
	job := deliveryJob{payload: &discord.WebhookPayload{}}
	for range deliveryQueueCapacity {
		if err := queue.enqueue(context.Background(), job); err != nil {
			t.Fatalf("enqueue() error while filling queue = %v", err)
		}
	}
	if err := queue.enqueue(context.Background(), job); !errors.Is(err, errDeliveryQueueFull) {
		t.Fatalf("enqueue() error = %v, want %v", err, errDeliveryQueueFull)
	}
}

func TestDeliveryQueueHonorsCancellation(t *testing.T) {
	t.Parallel()
	queue := newDeliveryQueue()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := queue.enqueue(ctx, deliveryJob{payload: &discord.WebhookPayload{}})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("enqueue() error = %v, want %v", err, context.Canceled)
	}
}

func TestAppRunStopsDeliveryWorkerWhenQueueIsFull(t *testing.T) {
	t.Parallel()
	streamer := &fakeStreamer{
		events:     make(chan news.Item, deliveryQueueCapacity+2),
		terminated: make(chan error, 1),
	}
	sender := &blockingSender{started: make(chan struct{}, 1)}
	app := newAppWithDeps(testSettings(), streamer, sender, slog.New(slog.NewTextHandler(io.Discard, nil)))
	runCh := make(chan error, 1)
	go func() { runCh <- app.Run(context.Background()) }()
	item := news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple launches product"}
	streamer.events <- item
	select {
	case <-sender.started:
	case <-time.After(time.Second):
		t.Fatal("delivery worker did not start")
	}
	for range deliveryQueueCapacity + 1 {
		streamer.events <- item
	}
	close(streamer.events)
	select {
	case err := <-runCh:
		if !errors.Is(err, errDeliveryQueueFull) {
			t.Fatalf("Run() error = %v, want %v", err, errDeliveryQueueFull)
		}
	case <-time.After(time.Second):
		t.Fatal("Run() did not stop after queue became full")
	}
}

type fakeStreamer struct {
	events     chan news.Item
	terminated chan error
	connectErr error
}

type fakeTradeStreamer struct {
	trades         chan alpaca.Trade
	terminated     chan error
	connectErr     error
	connectStarted chan struct{}
	connectDone    chan struct{}
	blockConnect   bool
}

type signalWriter struct {
	writes chan struct{}
	output bytes.Buffer
}

func (w *signalWriter) Write(value []byte) (int, error) {
	count, err := w.output.Write(value)
	select {
	case w.writes <- struct{}{}:
	default:
	}
	return count, err
}

func newFakeTradeStreamer() *fakeTradeStreamer {
	return &fakeTradeStreamer{trades: make(chan alpaca.Trade, 8), terminated: make(chan error, 1)}
}

func (f *fakeTradeStreamer) Connect(ctx context.Context) error {
	if f.connectStarted != nil {
		close(f.connectStarted)
	}
	if f.blockConnect {
		<-ctx.Done()
		if f.connectDone != nil {
			close(f.connectDone)
		}
		return ctx.Err()
	}
	return f.connectErr
}
func (f *fakeTradeStreamer) Trades() <-chan alpaca.Trade { return f.trades }
func (f *fakeTradeStreamer) Terminated() <-chan error    { return f.terminated }

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

type blockingSender struct {
	started chan struct{}
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

func (s *blockingSender) Send(ctx context.Context, _ []string, _ *discord.WebhookPayload) error {
	select {
	case s.started <- struct{}{}:
	default:
	}
	<-ctx.Done()
	return ctx.Err()
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
