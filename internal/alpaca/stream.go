package alpaca

import (
	"context"
	"time"

	"github.com/alpacahq/alpaca-trade-api-go/v3/marketdata"
	astream "github.com/alpacahq/alpaca-trade-api-go/v3/marketdata/stream"
	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/news"
)

const reconnectDelay = 5 * time.Second

var (
	newNewsClient  = astream.NewNewsClient
	newStockClient = astream.NewStocksClient
)

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

// NewTradeStreamer builds an SDK-backed Alpaca stock trade streamer.
func NewTradeStreamer(settings config.Settings) TradeStreamer {
	trades := make(chan Trade, 256)
	client := newStockClient(
		marketdata.IEX,
		astream.WithCredentials(settings.AlpacaAPIKey, settings.AlpacaAPISecret),
		astream.WithBaseURL(settings.AlpacaStockStreamURL),
		astream.WithReconnectSettings(0, reconnectDelay),
		astream.WithTrades(func(value astream.Trade) {
			trades <- tradeFromStockTrade(value)
		}, "SPY", "QQQ"),
	)
	return &sdkTradeStreamer{client: client, trades: trades}
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

func tradeFromStockTrade(value astream.Trade) Trade {
	return Trade{
		Symbol:     value.Symbol,
		Price:      value.Price,
		Size:       value.Size,
		Exchange:   value.Exchange,
		Timestamp:  value.Timestamp,
		Conditions: value.Conditions,
		Tape:       value.Tape,
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

type sdkTradeStreamer struct {
	client interface {
		connector
		terminatedReporter
	}
	trades <-chan Trade
}

// Connect opens the underlying SDK stock stream.
func (s *sdkTradeStreamer) Connect(ctx context.Context) error {
	return s.client.Connect(ctx)
}

// Trades returns decoded stock trades from the stream.
func (s *sdkTradeStreamer) Trades() <-chan Trade {
	return s.trades
}

// Terminated reports terminal SDK stock stream errors.
func (s *sdkTradeStreamer) Terminated() <-chan error {
	return s.client.Terminated()
}
