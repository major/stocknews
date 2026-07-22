// Package alpaca wraps the Alpaca news streaming client.
package alpaca

import (
	"context"

	"github.com/major/stocknews/internal/news"
)

// Streamer delivers Alpaca news items and reports terminal stream errors.
type Streamer interface {
	Connect(ctx context.Context) error
	Events() <-chan news.Item
	Terminated() <-chan error
}
