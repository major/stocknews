package earnings

import "testing"

func TestEarningsBehavior(t *testing.T) {
	t.Parallel()
	if !IsNews([]string{"AAPL"}, "AAPL Q1 EPS $2.00 vs $1.80 est.") {
		t.Fatal("expected earnings match")
	}
	for _, headline := range []string{"AAPL Q1 EPS $2.00 up from $1.80 estimate", "MSFT Q2 Sales $50B down from $48B estimate"} {
		if IsNews([]string{"AAPL"}, headline) {
			t.Fatalf("unexpected earnings match for %q", headline)
		}
	}
	if IsNews([]string{"AAPL", "MSFT"}, "AAPL Q1 EPS $2.00 vs $1.80 est.") {
		t.Fatal("expected multi-symbol rejection")
	}

	result := ExtractData("AAPL Q1 EPS $2.00 beat $1.80 Estimate")
	if got := result["EPS"]; got.Actual != "$2.00" || got.Estimate != "$1.80" || !got.Beat {
		t.Fatalf("unexpected EPS result: %#v", got)
	}
	result = ExtractData("MSFT Q2 Sales $50B beat $48B Estimate")
	if got := result["Sales"]; got.Actual != "$50B" || got.Estimate != "$48B" || !got.Beat {
		t.Fatalf("unexpected Sales result: %#v", got)
	}
	result = ExtractData("AAPL Q1 EPS $2.00 missed $2.10")
	if got := result["EPS"]; got.Beat || got.Estimate != "$2.10" {
		t.Fatalf("unexpected missed result: %#v", got)
	}
	if len(ExtractData("Apple announces new product launch")) != 0 {
		t.Fatal("expected empty result")
	}
	if CompanyName("Apple Q1 Earnings Report") != "Apple" || CompanyName("Q1 Earnings Report") != "" {
		t.Fatal("unexpected company name parsing")
	}
	if BeatEmoji(true) != "💚" || BeatEmoji(false) != "💔" {
		t.Fatal("unexpected emojis")
	}
	if Description("AAPL Q1 EPS $2.00 beat $1.80 Estimate") != "💚 EPS: $2.00 vs. $1.80 est." {
		t.Fatal("unexpected description")
	}
	if !HasBlockedPhrases("This is a spam headline", []string{"spam", "scam"}) || HasBlockedPhrases("This is a normal headline", []string{"spam", "scam"}) {
		t.Fatal("blocked phrase mismatch")
	}
	if !IsAnalystRatingChange("Apple price target raised to $200") || IsAnalystRatingChange("Apple releases new iPhone") {
		t.Fatal("analyst change mismatch")
	}
}
