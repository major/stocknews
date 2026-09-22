// Package alpaca wraps Alpaca market data streaming clients.
package alpaca

import (
	"context"
	"time"

	"github.com/major/stocknews/internal/news"
)

// Streamer delivers Alpaca news items and reports terminal stream errors.
type Streamer interface {
	Connect(ctx context.Context) error
	Events() <-chan news.Item
	Terminated() <-chan error
}

// Trade models one Alpaca stock trade.
type Trade struct {
	Symbol     string
	Price      float64
	Size       uint32
	Exchange   string
	Timestamp  time.Time
	Conditions []string
	Tape       string
}

// TradeStreamer delivers Alpaca stock trades and reports terminal stream errors.
type TradeStreamer interface {
	Connect(ctx context.Context) error
	Trades() <-chan Trade
	Terminated() <-chan error
}
