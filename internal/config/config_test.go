package config

import (
	"os"
	"testing"
)

func TestCSVValues(t *testing.T) {
	t.Parallel()
	got := CSVValues("spam, scam,, legit ")
	want := []string{"spam", "scam", "legit"}
	if len(got) != len(want) {
		t.Fatalf("len = %d, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestFromEnv(t *testing.T) {
	t.Setenv("ALPACA_API_KEY", "key")
	t.Setenv("ALPACA_API_SECRET", "secret")
	t.Setenv("ALPACA_NEWS_STREAM_URL", "")
	t.Setenv("STOCK_LOGO", "")
	t.Setenv("TRANSPARENT_PNG", "")
	unsetEnv(t, "ALPACA_NEWS_STREAM_URL")
	unsetEnv(t, "STOCK_LOGO")
	unsetEnv(t, "TRANSPARENT_PNG")
	t.Setenv("DISCORD_ANALYST_WEBHOOKS", "a, b")
	t.Setenv("DISCORD_EARNINGS_WEBHOOKS", "c")
	t.Setenv("DISCORD_NEWS_WEBHOOKS", "d,e")
	t.Setenv("BLOCKED_PHRASES", "spam, eggs")

	settings, err := FromEnv()
	if err != nil {
		t.Fatalf("FromEnv() error = %v", err)
	}
	if settings.AlpacaNewsStreamURL != defaultNewsStreamURL || settings.StockLogo != defaultStockLogo || settings.TransparentPNG != defaultTransparent {
		t.Fatal("defaults not applied")
	}
	if len(settings.DiscordNewsWebhooks) != 2 || settings.DiscordNewsWebhooks[1] != "e" {
		t.Fatalf("unexpected news webhooks: %#v", settings.DiscordNewsWebhooks)
	}
	if len(settings.BlockedPhrases) != 2 || settings.BlockedPhrases[0] != "spam" {
		t.Fatalf("unexpected blocked phrases: %#v", settings.BlockedPhrases)
	}
}

func TestFromEnvMissingRequired(t *testing.T) {
	tests := []struct {
		name     string
		key      string
		value    string
		setValue bool
	}{
		{name: "missing ALPACA_API_KEY", key: "ALPACA_API_KEY"},
		{name: "missing ALPACA_API_SECRET", key: "ALPACA_API_SECRET"},
		{name: "blank ALPACA_API_KEY", key: "ALPACA_API_KEY", value: "   ", setValue: true},
		{name: "blank ALPACA_API_SECRET", key: "ALPACA_API_SECRET", value: "\t", setValue: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			unsetEnv(t, "ALPACA_API_KEY")
			unsetEnv(t, "ALPACA_API_SECRET")
			t.Setenv("ALPACA_API_KEY", "key")
			t.Setenv("ALPACA_API_SECRET", "secret")
			if tc.setValue {
				t.Setenv(tc.key, tc.value)
			} else {
				unsetEnv(t, tc.key)
			}
			_, err := FromEnv()
			if err == nil {
				t.Fatal("expected error")
			}
		})
	}
}

func unsetEnv(t *testing.T, key string) {
	t.Helper()
	old, ok := os.LookupEnv(key)
	if ok {
		t.Cleanup(func() { _ = os.Setenv(key, old) })
	} else {
		t.Cleanup(func() { _ = os.Unsetenv(key) })
	}
	_ = os.Unsetenv(key)
}
