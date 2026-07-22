package discord

import (
	"fmt"
	"strings"

	"github.com/major/stocknews/internal/analyst"
	"github.com/major/stocknews/internal/earnings"
	"github.com/major/stocknews/internal/news"
)

// WebhookPayload is the webhook request body.
type WebhookPayload struct {
	Embeds []Embed `json:"embeds"`
}

// Embed is one Discord embed object.
type Embed struct {
	Title       string `json:"title"`
	Description string `json:"description,omitempty"`
	Color       *int   `json:"color,omitempty"`
	URL         string `json:"url,omitempty"`
	Image       Image  `json:"image"`
	Thumbnail   Image  `json:"thumbnail"`
}

// Image stores an embed image URL.
type Image struct {
	URL string `json:"url"`
}

// EarningsPayload builds the earnings webhook body.
func EarningsPayload(symbol, headline, stockLogo, transparentPNG string) (*WebhookPayload, bool) {
	description := earnings.Description(headline)
	if description == "" {
		return nil, false
	}
	return &WebhookPayload{Embeds: []Embed{{Title: symbol + ": " + earnings.CompanyName(headline), Description: description, Image: Image{URL: transparentPNG}, Thumbnail: Image{URL: logoURL(stockLogo, symbol)}}}}, true
}

// AnalystPayload builds the analyst webhook body.
func AnalystPayload(symbol, headline, stockLogo, transparentPNG string) (*WebhookPayload, bool) {
	report := analyst.Parse(headline)
	if report.PriceTargetAction != nil {
		switch *report.PriceTargetAction {
		case analyst.PriceTargetActionAnnounces, analyst.PriceTargetActionMaintains:
			return nil, false
		case analyst.PriceTargetActionLowers:
			return embedPayload("💔", 0xd42020, symbol, headline, report.Stock, report.PriceTarget, stockLogo, transparentPNG), true
		case analyst.PriceTargetActionRaises:
			return embedPayload("💚", 0x4caf50, symbol, headline, report.Stock, report.PriceTarget, stockLogo, transparentPNG), true
		}
	}
	return embedPayload("❓", 0, symbol, headline, report.Stock, report.PriceTarget, stockLogo, transparentPNG), true
}

// NewsPayload builds the general-news webhook body.
func NewsPayload(item news.Item, stockLogo, transparentPNG string) (*WebhookPayload, bool) {
	if len(item.Symbols) == 0 {
		return nil, false
	}
	return &WebhookPayload{Embeds: []Embed{{Title: item.Symbols[0] + ": " + item.Headline, Description: item.Summary, URL: item.URL, Image: Image{URL: transparentPNG}, Thumbnail: Image{URL: logoURL(stockLogo, item.Symbols[0])}}}}, true
}

func embedPayload(emoji string, color int, symbol, headline, stock string, price float64, stockLogo, transparentPNG string) *WebhookPayload {
	return &WebhookPayload{Embeds: []Embed{{Title: fmt.Sprintf("%s %s: %s $%.2f", emoji, symbol, stock, price), Description: headline, Color: &color, Image: Image{URL: transparentPNG}, Thumbnail: Image{URL: logoURL(stockLogo, symbol)}}}}
}

func logoURL(template, symbol string) string {
	return strings.ReplaceAll(template, "%s", strings.ToLower(symbol))
}
