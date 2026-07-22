// Package config loads runtime settings from environment variables.
package config

import (
	"fmt"
	"os"
	"strings"
)

const (
	defaultNewsStreamURL = "wss://stream.data.alpaca.markets/v1beta1/news"
	defaultStockLogo     = "https://static.stocktitan.net/company-logo/%s.webp"
	defaultTransparent   = "https://major.io/transparent.png"
	defaultBlocked       = "if you invested,you would have,would be worth"
)

// Settings stores runtime configuration loaded from the environment.
type Settings struct {
	AlpacaAPIKey            string
	AlpacaAPISecret         string
	AlpacaNewsStreamURL     string
	DiscordAnalystWebhooks  []string
	DiscordEarningsWebhooks []string
	DiscordNewsWebhooks     []string
	StockLogo               string
	TransparentPNG          string
	BlockedPhrases          []string
}

// FromEnv loads settings from the current process environment.
func FromEnv() (Settings, error) {
	apiKey, err := requiredEnv("ALPACA_API_KEY")
	if err != nil {
		return Settings{}, err
	}
	apiSecret, err := requiredEnv("ALPACA_API_SECRET")
	if err != nil {
		return Settings{}, err
	}

	return Settings{
		AlpacaAPIKey:            apiKey,
		AlpacaAPISecret:         apiSecret,
		AlpacaNewsStreamURL:     envString("ALPACA_NEWS_STREAM_URL", defaultNewsStreamURL),
		DiscordAnalystWebhooks:  CSVValues(envString("DISCORD_ANALYST_WEBHOOKS", "")),
		DiscordEarningsWebhooks: CSVValues(envString("DISCORD_EARNINGS_WEBHOOKS", "")),
		DiscordNewsWebhooks:     CSVValues(envString("DISCORD_NEWS_WEBHOOKS", "")),
		StockLogo:               envString("STOCK_LOGO", defaultStockLogo),
		TransparentPNG:          envString("TRANSPARENT_PNG", defaultTransparent),
		BlockedPhrases:          CSVValues(envString("BLOCKED_PHRASES", defaultBlocked)),
	}, nil
}

// CSVValues splits comma-separated values, trims whitespace, and removes empties.
func CSVValues(value string) []string {
	parts := strings.Split(value, ",")
	values := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			values = append(values, part)
		}
	}
	return values
}

func requiredEnv(key string) (string, error) {
	value, ok := os.LookupEnv(key)
	if !ok || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%s is required", key)
	}
	return value, nil
}

func envString(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
