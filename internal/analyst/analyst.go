// Package analyst parses analyst-rating headlines.
package analyst

import (
	"fmt"
	"regexp"
	"strings"
)

var (
	maintainsRE   = regexp.MustCompile(`(?i)^([\w\s]+) (Maintains|Reiterates) (.*) on (.+),`)
	actionRE      = regexp.MustCompile(`(?i)^([\w\s]+) (Downgrades|Upgrades|Initiates Coverage) (?:on\s)*([\w\s]+) (?:with|to) (.+),`)
	priceRE       = regexp.MustCompile(`\$([\d\.]+)`)
	priceActionRE = regexp.MustCompile(`, (Lowers|Maintains|Raises|Announces)`)
)

// PriceTargetAction describes the reported target change direction.
type PriceTargetAction string

const (
	// PriceTargetActionLowers marks a lower target.
	PriceTargetActionLowers PriceTargetAction = "Lowers"
	// PriceTargetActionRaises marks a higher target.
	PriceTargetActionRaises PriceTargetAction = "Raises"
	// PriceTargetActionAnnounces marks a newly announced target.
	PriceTargetActionAnnounces PriceTargetAction = "Announces"
	// PriceTargetActionMaintains marks an unchanged target.
	PriceTargetActionMaintains PriceTargetAction = "Maintains"
)

// News stores parsed analyst headline fields.
type News struct {
	Headline          string
	Firm              string
	Action            string
	Guidance          string
	Stock             string
	PriceTargetAction *PriceTargetAction
	PriceTarget       float64
}

// Parse extracts analyst headline fields.
func Parse(headline string) News {
	firm, action, guidance, stock := parseAnalystAction(headline)
	result := News{
		Headline:    headline,
		Firm:        firm,
		Action:      action,
		Guidance:    guidance,
		Stock:       stock,
		PriceTarget: parsePrice(headline),
	}
	if match := priceActionRE.FindStringSubmatch(headline); len(match) == 2 {
		action := PriceTargetAction(match[1])
		result.PriceTargetAction = &action
	}
	return result
}

func parseAnalystAction(headline string) (string, string, string, string) {
	if match := maintainsRE.FindStringSubmatch(headline); len(match) == 5 {
		return match[1], match[2], trimRating(match[3]), match[4]
	}
	if match := actionRE.FindStringSubmatch(headline); len(match) == 5 {
		return match[1], match[2], trimRating(match[4]), match[3]
	}
	return "", "", "", ""
}

func trimRating(value string) string {
	value = regexp.MustCompile(`Rating$`).ReplaceAllString(value, "")
	value = regexp.MustCompile(`\s+`).ReplaceAllString(value, " ")
	return strings.TrimSpace(value)
}

func parsePrice(headline string) float64 {
	match := priceRE.FindStringSubmatch(headline)
	if len(match) != 2 {
		return 0
	}
	var dollars float64
	_, _ = fmt.Sscanf(match[1], "%f", &dollars)
	return dollars
}
