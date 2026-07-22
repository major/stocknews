package analyst

import "testing"

func TestParse(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name        string
		headline    string
		firm        string
		action      string
		guidance    string
		stock       string
		priceAction *PriceTargetAction
		price       float64
	}{
		{name: "maintains", headline: "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223", firm: "Goldman Sachs", action: "Maintains", guidance: "Buy", stock: "Apple", priceAction: ptr(PriceTargetActionRaises), price: 223},
		{name: "upgrades", headline: "Morgan Stanley Upgrades Tesla to Overweight, Raises Price Target to $400", firm: "Morgan Stanley", action: "Upgrades", guidance: "Overweight", stock: "Tesla", priceAction: ptr(PriceTargetActionRaises), price: 400},
		{name: "downgrades", headline: "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135", firm: "JPMorgan", action: "Downgrades", guidance: "Neutral", stock: "Amazon", priceAction: ptr(PriceTargetActionLowers), price: 135},
		{name: "initiates", headline: "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850", firm: "Piper Sandler", action: "Initiates Coverage", guidance: "Overweight", stock: "Nvidia", priceAction: ptr(PriceTargetActionAnnounces), price: 850},
		{name: "maintains price", headline: "Morgan Stanley Upgrades Tesla to Overweight, Maintains Price Target at $400", firm: "Morgan Stanley", action: "Upgrades", guidance: "Overweight", stock: "Tesla", priceAction: ptr(PriceTargetActionMaintains), price: 400},
		{name: "no action", headline: "Apple releases new iPhone"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			news := Parse(tt.headline)
			if news.Firm != tt.firm || news.Action != tt.action || news.Guidance != tt.guidance || news.Stock != tt.stock || news.PriceTarget != tt.price {
				t.Fatalf("unexpected parse result: %#v", news)
			}
			if !sameAction(news.PriceTargetAction, tt.priceAction) {
				t.Fatalf("price action = %v, want %v", news.PriceTargetAction, tt.priceAction)
			}
		})
	}
}

func TestParseStripsRatingSuffix(t *testing.T) {
	news := Parse("UBS Downgrades Microsoft to Neutral Rating, Lowers Price Target to $275")
	if news.Guidance != "Neutral" {
		t.Fatalf("guidance = %q", news.Guidance)
	}
}

func sameAction(left, right *PriceTargetAction) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return *left == *right
}

func ptr(value PriceTargetAction) *PriceTargetAction {
	return &value
}
