package alpaca

import (
	"context"
	"time"

	astream "github.com/alpacahq/alpaca-trade-api-go/v3/marketdata/stream"
	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/news"
)

const reconnectDelay = 5 * time.Second

var newNewsClient = astream.NewNewsClient

// NewStreamer builds an SDK-backed Alpaca news streamer.
func NewStreamer(settings config.Settings) Streamer {
	events := make(chan news.Item, 256)
	client := newNewsClient(
		astream.WithCredentials(settings.AlpacaAPIKey, settings.AlpacaAPISecret),
		astream.WithBaseURL(settings.AlpacaNewsStreamURL),
		astream.WithReconnectSettings(0, reconnectDelay),
		astream.WithNews(func(value astream.News) {
			events <- itemFromNews(value)
		}, "*"),
	)
	return &sdkStreamer{client: client, events: events}
}

func itemFromNews(value astream.News) news.Item {
	return news.Item{
		Symbols:  value.Symbols,
		Author:   value.Author,
		Headline: value.Headline,
		Summary:  value.Summary,
		URL:      value.URL,
	}
}

type terminatedReporter interface {
	Terminated() <-chan error
}

type connector interface {
	Connect(ctx context.Context) error
}

type sdkStreamer struct {
	client interface {
		connector
		terminatedReporter
	}
	events <-chan news.Item
}

// Connect opens the underlying SDK stream.
func (s *sdkStreamer) Connect(ctx context.Context) error {
	return s.client.Connect(ctx)
}

// Events returns decoded news items from the stream.
func (s *sdkStreamer) Events() <-chan news.Item {
	return s.events
}

// Terminated reports terminal SDK stream errors.
func (s *sdkStreamer) Terminated() <-chan error {
	return s.client.Terminated()
}
