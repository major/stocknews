// Package earnings parses earnings headlines and summaries.
package earnings

import (
	"regexp"
	"sort"
	"strings"
)

var (
	earningsNewsRE = regexp.MustCompile(`(EPS|Sales) (~*\$[\d\.\(\)\-\$\~]+[KMB]*) [\w\s]+ (\$[\d\.\(\)\-\$\~]+[KMB]*)`)
	earningsDataRE = regexp.MustCompile(`(?i)(EPS|Sales) ([\d\.()$KMBT]+) (\w+) ([\d\.()$KMBT]+)(?: Est(?:imate|\.)?)?`)
	companyRE      = regexp.MustCompile(`^(.*?) Q[1-4]`)
)

// Result stores actual, estimate, and beat state for one earnings field.
type Result struct {
	Actual   string
	Estimate string
	Beat     bool
}

// IsNews reports whether a headline matches the earnings pattern.
func IsNews(symbols []string, headline string) bool {
	headlineLower := strings.ToLower(headline)
	return len(symbols) == 1 && !strings.Contains(headlineLower, "up from") && !strings.Contains(headlineLower, "down from") && earningsNewsRE.MatchString(headline)
}

// ExtractData parses earnings data keyed by EPS or Sales.
func ExtractData(headline string) map[string]Result {
	results := make(map[string]Result)
	for _, match := range earningsDataRE.FindAllStringSubmatch(headline, -1) {
		kind := strings.ToUpper(match[1])
		if kind == "SALES" {
			kind = "Sales"
		} else {
			kind = "EPS"
		}
		results[kind] = Result{Actual: match[2], Estimate: match[4], Beat: ParseResult(match[3])}
	}
	return results
}

// ParseResult reports whether a result string indicates a beat.
func ParseResult(raw string) bool {
	return strings.Contains(strings.ToLower(raw), "beat")
}

// CompanyName returns the company name prefix from a quarterly headline.
func CompanyName(headline string) string {
	match := companyRE.FindStringSubmatch(headline)
	if len(match) != 2 {
		return ""
	}
	return match[1]
}

// BeatEmoji returns the display marker for a beat or miss.
func BeatEmoji(value bool) string {
	if value {
		return "💚"
	}
	return "💔"
}

// Description builds the earnings summary body.
func Description(headline string) string {
	data := ExtractData(headline)
	lines := make([]string, 0, len(data))
	for key, value := range data {
		lines = append(lines, BeatEmoji(value.Beat)+" "+key+": "+value.Actual+" vs. "+value.Estimate+" est.")
	}
	sort.Strings(lines)
	return strings.Join(lines, "\n")
}

// HasBlockedPhrases reports whether a headline contains any blocked phrase.
func HasBlockedPhrases(headline string, blockedPhrases []string) bool {
	headline = strings.ToLower(headline)
	for _, phrase := range blockedPhrases {
		if strings.Contains(headline, strings.ToLower(phrase)) {
			return true
		}
	}
	return false
}

// IsAnalystRatingChange reports whether a headline mentions a price target.
func IsAnalystRatingChange(headline string) bool {
	return strings.Contains(strings.ToLower(headline), "price target")
}
