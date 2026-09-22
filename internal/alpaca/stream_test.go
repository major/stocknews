package alpaca

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	astream "github.com/alpacahq/alpaca-trade-api-go/v3/marketdata/stream"
	"github.com/coder/websocket"
	"github.com/major/stocknews/internal/config"
	"github.com/major/stocknews/internal/news"
	"github.com/vmihailenco/msgpack/v5"
)

func TestNewStreamer(t *testing.T) {
	t.Parallel()
	streamer := NewStreamer(config.Settings{AlpacaAPIKey: "key", AlpacaAPISecret: "secret", AlpacaNewsStreamURL: "ws://example.test/news"})
	if streamer == nil {
		t.Fatal("expected streamer")
	}
}

func TestNewTradeStreamer(t *testing.T) {
	t.Parallel()
	streamer := NewTradeStreamer(config.Settings{AlpacaAPIKey: "key", AlpacaAPISecret: "secret", AlpacaStockStreamURL: "ws://example.test"})
	if streamer == nil {
		t.Fatal("expected trade streamer")
	}
}

func TestNewTradeStreamerEmitsTrades(t *testing.T) {
	t.Parallel()
	hold := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		connection, err := websocket.Accept(writer, request, nil)
		if err != nil {
			return
		}
		defer func() {
			if err := connection.Close(websocket.StatusNormalClosure, ""); err != nil {
				t.Errorf("close websocket: %v", err)
			}
		}()
		ctx := request.Context()
		if err := writeStreamMessage(ctx, connection, []streamSuccessMessage{{Type: "success", Message: "connected"}}); err != nil {
			return
		}
		if _, _, err := connection.Read(ctx); err != nil {
			return
		}
		if err := writeStreamMessage(ctx, connection, []streamSuccessMessage{{Type: "success", Message: "authenticated"}}); err != nil {
			return
		}
		if _, _, err := connection.Read(ctx); err != nil {
			return
		}
		if err := writeStreamMessage(ctx, connection, []streamSubscriptionMessage{{
			Type:         "subscription",
			Trades:       []string{"SPY", "QQQ"},
			CancelErrors: []string{"SPY", "QQQ"},
			Corrections:  []string{"SPY", "QQQ"},
		}}); err != nil {
			return
		}
		_ = writeStreamMessage(ctx, connection, []streamTradeMessage{{
			Type:       "t",
			Symbol:     "SPY",
			Price:      500.25,
			Size:       100,
			Exchange:   "V",
			Timestamp:  time.Date(2026, time.September, 21, 14, 30, 0, 0, time.UTC),
			Conditions: []string{"@", "F"},
			Tape:       "C",
		}})
		<-hold
	}))
	ctx, cancel := context.WithCancel(context.Background())
	defer func() {
		close(hold)
		cancel()
		server.Close()
	}()

	streamer := NewTradeStreamer(config.Settings{
		AlpacaAPIKey:         "key",
		AlpacaAPISecret:      "secret",
		AlpacaStockStreamURL: server.URL,
	})
	connectErr := make(chan error, 1)
	go func() { connectErr <- streamer.Connect(ctx) }()
	select {
	case got := <-streamer.Trades():
		if got.Symbol != "SPY" || got.Price != 500.25 || got.Size != 100 || got.Exchange != "V" || got.Tape != "C" || len(got.Conditions) != 2 {
			t.Fatalf("trade = %+v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for stock trade")
	}
	if err := <-connectErr; err != nil {
		t.Fatalf("Connect() error = %v", err)
	}
}

func writeStreamMessage(ctx context.Context, connection *websocket.Conn, value any) error {
	message, err := msgpack.Marshal(value)
	if err != nil {
		return err
	}
	return connection.Write(ctx, websocket.MessageBinary, message)
}

type streamSuccessMessage struct {
	Type    string `msgpack:"T"`
	Message string `msgpack:"msg"`
}

type streamSubscriptionMessage struct {
	Type         string   `msgpack:"T"`
	Trades       []string `msgpack:"trades"`
	CancelErrors []string `msgpack:"cancelErrors"`
	Corrections  []string `msgpack:"corrections"`
}

type streamTradeMessage struct {
	Type       string    `msgpack:"T"`
	Symbol     string    `msgpack:"S"`
	Price      float64   `msgpack:"p"`
	Size       uint32    `msgpack:"s"`
	Exchange   string    `msgpack:"x"`
	Timestamp  time.Time `msgpack:"t"`
	Conditions []string  `msgpack:"c"`
	Tape       string    `msgpack:"z"`
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

func TestSDKTradeStreamerDelegates(t *testing.T) {
	t.Parallel()
	trades := make(chan Trade, 1)
	terminated := make(chan error, 1)
	client := &fakeSDKClient{terminated: terminated}
	streamer := &sdkTradeStreamer{client: client, trades: trades}
	trades <- Trade{Symbol: "QQQ"}
	terminated <- errors.New("done")
	if err := streamer.Connect(context.Background()); err != nil {
		t.Fatalf("Connect() error = %v", err)
	}
	if got := (<-streamer.Trades()).Symbol; got != "QQQ" {
		t.Fatalf("symbol = %q", got)
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

func TestTradeFromStockTrade(t *testing.T) {
	t.Parallel()
	timestamp := time.Date(2026, time.September, 21, 14, 30, 0, 0, time.UTC)
	got := tradeFromStockTrade(astream.Trade{
		Symbol:     "SPY",
		Price:      500.25,
		Size:       100,
		Exchange:   "V",
		Timestamp:  timestamp,
		Conditions: []string{"@", "F"},
		Tape:       "C",
	})
	if got.Symbol != "SPY" || got.Price != 500.25 || got.Size != 100 || got.Exchange != "V" || !got.Timestamp.Equal(timestamp) || got.Tape != "C" || len(got.Conditions) != 2 {
		t.Fatalf("tradeFromStockTrade() = %+v", got)
	}
}

type fakeSDKClient struct {
	terminated chan error
}

func (*fakeSDKClient) Connect(context.Context) error { return nil }
func (f *fakeSDKClient) Terminated() <-chan error    { return f.terminated }
