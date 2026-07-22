package news

import (
	"encoding/json"
	"testing"
)

func testItem(symbols []string, author, headline string) Item {
	return Item{Symbols: symbols, Author: author, Headline: headline}
}

func TestNewsBehavior(t *testing.T) {
	t.Parallel()
	if symbol, ok := AcceptedSymbol(testItem([]string{"AAPL"}, "Benzinga Newsdesk", "headline")); !ok || symbol != "AAPL" {
		t.Fatal("expected accepted symbol")
	}
	for _, item := range []Item{testItem([]string{"AAPL", "MSFT"}, "Benzinga Newsdesk", "headline"), testItem([]string{"TSX:SHOP"}, "Benzinga Newsdesk", "headline"), testItem([]string{""}, "Benzinga Newsdesk", "headline"), testItem([]string{"   \t"}, "Benzinga Newsdesk", "headline")} {
		if _, ok := AcceptedSymbol(item); ok {
			t.Fatalf("expected rejection for %#v", item)
		}
	}
	reasonCases := []struct {
		want SkipReason
		item Item
	}{
		{want: SkipReasonSymbolCount, item: testItem([]string{"AAPL", "MSFT"}, "Benzinga Newsdesk", "headline")},
		{want: SkipReasonEmptySymbol, item: testItem([]string{""}, "Benzinga Newsdesk", "headline")},
		{want: SkipReasonEmptySymbol, item: testItem([]string{"   "}, "Benzinga Newsdesk", "headline")},
		{want: SkipReasonNonUSExchange, item: testItem([]string{"TSX:SHOP"}, "Benzinga Newsdesk", "headline")},
		{want: SkipReasonUnapprovedAuthor, item: testItem([]string{"AAPL"}, "Someone Else", "AAPL Q1 EPS $2.00 vs $1.80 est.")},
	}
	for _, tc := range reasonCases {
		if got, ok := Reason(tc.item); !ok || got != tc.want {
			t.Fatalf("reason = %q, %v; want %q", got, ok, tc.want)
		}
	}
	if got, ok := Reason(testItem([]string{"AAPL"}, "Benzinga Newsdesk", "headline")); ok || got != "" {
		t.Fatalf("expected accepted item to have no skip reason, got %q, %v", got, ok)
	}
	if _, ok := Classify(testItem([]string{"AAPL"}, "Someone Else", "AAPL Q1 EPS $2.00 vs $1.80 est.")); ok {
		t.Fatal("expected rejected classification")
	}
	classified := map[Kind]Item{
		KindEarnings: testItem([]string{"AAPL"}, "Benzinga Newsdesk", "AAPL Q1 EPS $2.00 vs $1.80 est."),
		KindAnalyst:  testItem([]string{"AAPL"}, "Benzinga Newsdesk", "Baird Upgrades Apple to Outperform, Raises Price Target to $200"),
		KindNews:     testItem([]string{"AAPL"}, "Benzinga Newsdesk", "Apple releases new iPhone"),
	}
	for want, item := range classified {
		if got, ok := Classify(item); !ok || got != want {
			t.Fatalf("kind = %q, %v; want %q", got, ok, want)
		}
	}
	if got, ok := Classify(testItem([]string{"AAPL"}, "Benzinga Newsdesk", "Apple price target raised to $200")); !ok || got != KindNews {
		t.Fatalf("kind = %q, %v; want %q", got, ok, KindNews)
	}
	var item Item
	if err := json.Unmarshal([]byte(`{"headline":"hi"}`), &item); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if item.Headline != "hi" || item.Symbols != nil {
		t.Fatalf("unexpected item: %#v", item)
	}
}
