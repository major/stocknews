package alpaca

import (
	"context"
	"errors"
	"testing"

	astream "github.com/alpacahq/alpaca-trade-api-go/v3/marketdata/stream"
	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/news"
)

func TestNewStreamer(t *testing.T) {
	t.Parallel()
	streamer := NewStreamer(config.Settings{AlpacaAPIKey: "key", AlpacaAPISecret: "secret", AlpacaNewsStreamURL: "ws://example.test/news"})
	if streamer == nil {
		t.Fatal("expected streamer")
	}
}

func TestSDKStreamerDelegates(t *testing.T) {
	t.Parallel()
	events := make(chan news.Item, 1)
	terminated := make(chan error, 1)
	client := &fakeSDKClient{terminated: terminated}
	streamer := &sdkStreamer{client: client, events: events}
	events <- news.Item{Headline: "hi"}
	terminated <- errors.New("done")
	if err := streamer.Connect(context.Background()); err != nil {
		t.Fatalf("Connect() error = %v", err)
	}
	if got := (<-streamer.Events()).Headline; got != "hi" {
		t.Fatalf("headline = %q", got)
	}
	if err := <-streamer.Terminated(); err == nil || err.Error() != "done" {
		t.Fatalf("Terminated() = %v", err)
	}
}

func TestItemFromNews(t *testing.T) {
	t.Parallel()
	got := itemFromNews(astream.News{
		Symbols:  []string{"AAPL"},
		Author:   "Benzinga Newsdesk",
		Headline: "headline",
		Summary:  "summary",
		URL:      "https://example.test",
	})
	if got.Author != "Benzinga Newsdesk" || got.Headline != "headline" || got.Summary != "summary" || got.URL != "https://example.test" || len(got.Symbols) != 1 || got.Symbols[0] != "AAPL" {
		t.Fatalf("itemFromNews() = %+v", got)
	}
}

type fakeSDKClient struct {
	terminated chan error
}

func (*fakeSDKClient) Connect(context.Context) error { return nil }
func (f *fakeSDKClient) Terminated() <-chan error    { return f.terminated }
