package discord

import (
	"encoding/json"
	"testing"

	"github.com/major/stocknews/internal/news"
)

const (
	logoURLTemplate = "https://static.stocktitan.net/company-logo/%s.webp"
	transparentURL  = "https://major.io/transparent.png"
)

func TestPayloadBuilders(t *testing.T) {
	t.Parallel()
	payload, ok := EarningsPayload("AAPL", "Apple Q1 EPS $2.00 beat $1.80 Estimate", logoURLTemplate, transparentURL)
	if !ok {
		t.Fatal("expected earnings payload")
	}
	assertJSON(t, payload, `{"embeds":[{"title":"AAPL: Apple","description":"💚 EPS: $2.00 vs. $1.80 est.","image":{"url":"https://major.io/transparent.png"},"thumbnail":{"url":"https://static.stocktitan.net/company-logo/aapl.webp"}}]}`)
	if _, ok := EarningsPayload("AAPL", "Apple launches product", logoURLTemplate, transparentURL); ok {
		t.Fatal("expected empty earnings payload")
	}
	if payload, ok = AnalystPayload("AAPL", "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223", logoURLTemplate, transparentURL); !ok || payload.Embeds[0].Title != "💚 AAPL: Apple $223.00" {
		t.Fatalf("unexpected raises payload: %#v %v", payload, ok)
	}
	if payload, ok = AnalystPayload("AMZN", "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135", logoURLTemplate, transparentURL); !ok || *payload.Embeds[0].Color != 0xd42020 {
		t.Fatalf("unexpected lowers payload: %#v %v", payload, ok)
	}
	if payload, ok = AnalystPayload("AAPL", "Goldman Sachs Maintains Buy on Apple, Says Fundamentals Remain Strong", logoURLTemplate, transparentURL); !ok || *payload.Embeds[0].Color != 0 {
		t.Fatalf("unexpected unknown payload: %#v %v", payload, ok)
	}
	if _, ok := AnalystPayload("NVDA", "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850", logoURLTemplate, transparentURL); ok {
		t.Fatal("expected announces skip")
	}
	item := news.Item{Symbols: []string{"AAPL"}, Author: "Benzinga Newsdesk", Headline: "Apple releases new iPhone", Summary: "summary", URL: "https://example.test"}
	if payload, ok = NewsPayload(item, logoURLTemplate, transparentURL); !ok || payload.Embeds[0].URL != "https://example.test" {
		t.Fatalf("unexpected news payload: %#v %v", payload, ok)
	}
	if _, ok := NewsPayload(news.Item{}, logoURLTemplate, transparentURL); ok {
		t.Fatal("expected empty news payload")
	}
}

func assertJSON(t *testing.T, value any, want string) {
	t.Helper()
	gotBytes, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal got: %v", err)
	}
	wantBytes := []byte(want)
	if string(gotBytes) != string(wantBytes) {
		t.Fatalf("json = %s, want %s", gotBytes, wantBytes)
	}
}
