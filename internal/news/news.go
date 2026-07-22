// Package news filters and classifies incoming news items.
package news

import (
	"strings"

	"github.com/major/stocknews/internal/analyst"
	"github.com/major/stocknews/internal/earnings"
)

// Item models one Alpaca news message.
type Item struct {
	Symbols  []string `json:"symbols,omitempty"`
	Author   string   `json:"author,omitempty"`
	Headline string   `json:"headline,omitempty"`
	Summary  string   `json:"summary,omitempty"`
	URL      string   `json:"url,omitempty"`
}

// Kind identifies the destination class for a news item.
type Kind string

const (
	// KindEarnings routes earnings headlines.
	KindEarnings Kind = "earnings"
	// KindAnalyst routes analyst headlines.
	KindAnalyst Kind = "analyst"
	// KindNews routes general headlines.
	KindNews Kind = "news"
)

// SkipReason explains why an item was ignored.
type SkipReason string

const (
	// SkipReasonSymbolCount rejects items without exactly one symbol.
	SkipReasonSymbolCount SkipReason = "symbol_count"
	// SkipReasonEmptySymbol rejects empty symbols.
	SkipReasonEmptySymbol SkipReason = "empty_symbol"
	// SkipReasonNonUSExchange rejects symbols with exchange prefixes.
	SkipReasonNonUSExchange SkipReason = "non_us_exchange"
	// SkipReasonUnapprovedAuthor rejects non-Benzinga authors.
	SkipReasonUnapprovedAuthor SkipReason = "unapproved_author"
)

// AcceptedSymbol returns the approved symbol when present.
func AcceptedSymbol(item Item) (string, bool) {
	if len(item.Symbols) != 1 {
		return "", false
	}
	symbol := strings.TrimSpace(item.Symbols[0])
	if symbol == "" || containsColon(symbol) {
		return "", false
	}
	return symbol, true
}

// Reason returns the first skip reason for an item, if any.
func Reason(item Item) (SkipReason, bool) {
	if len(item.Symbols) != 1 {
		return SkipReasonSymbolCount, true
	}
	symbol := strings.TrimSpace(item.Symbols[0])
	if symbol == "" {
		return SkipReasonEmptySymbol, true
	}
	if containsColon(symbol) {
		return SkipReasonNonUSExchange, true
	}
	if !IsAllowedAuthor(item) {
		return SkipReasonUnapprovedAuthor, true
	}
	return "", false
}

// IsAllowedAuthor reports whether the item author is approved.
func IsAllowedAuthor(item Item) bool {
	return item.Author == "Benzinga Newsdesk"
}

// Classify determines the item kind when it is accepted.
func Classify(item Item) (Kind, bool) {
	if _, ok := AcceptedSymbol(item); !ok || !IsAllowedAuthor(item) {
		return "", false
	}
	if earnings.IsNews(item.Symbols, item.Headline) {
		return KindEarnings, true
	}
	parsed := analyst.Parse(item.Headline)
	if parsed.Action != "" && parsed.Stock != "" {
		return KindAnalyst, true
	}
	return KindNews, true
}

func containsColon(value string) bool {
	for _, r := range value {
		if r == ':' {
			return true
		}
	}
	return false
}
