package contextsearch

import (
	"regexp"
	"strings"

	"github.com/SimonZhou96/MoneyManager/stock_screener/tavily_fetcher/internal/search"
)

func SearchCompaniesBatch(provider search.Provider, market string, rows []search.ScreeningSignalRow, maxResults int, maxChars int) (map[string][]search.SearchDocument, error) {
	grouped := make(map[string][]search.SearchDocument, len(rows))
	if len(rows) == 0 {
		return grouped, nil
	}
	if maxChars <= 0 {
		maxChars = 390
	}
	for _, row := range rows {
		grouped[row.Code] = nil
	}
	for _, batch := range splitRowsByQueryBudget(market, rows, maxChars) {
		query := buildCompanyBatchQuery(market, batch, maxChars)
		batchMax := maxResults
		if batchMax < len(batch) {
			batchMax = len(batch)
		}
		docs, err := provider.Search(query, batchMax)
		if err != nil {
			return grouped, err
		}
		assigned := assignDocumentsToStocks(docs, batch)
		for code, codeDocs := range assigned {
			grouped[code] = dedupeDocuments(append(grouped[code], codeDocs...))
		}
	}
	return grouped, nil
}

func buildCompanyBatchQuery(market string, rows []search.ScreeningSignalRow, maxChars int) string {
	prefix := strings.ToUpper(market) + " stocks latest news earnings events official filing annual report: "
	if len(rows) > 0 {
		allETF := true
		for _, row := range rows {
			if !row.IsETF {
				allETF = false
				break
			}
		}
		if allETF {
			prefix = strings.ToUpper(market) + " ETF fund tracking index theme sector macro news: "
		}
	}
	for _, nameMax := range []int{36, 30, 24, 18, 12, 8} {
		query := composeCompanyBatchQuery(prefix, rows, nameMax)
		if len(query) <= maxChars {
			return query
		}
	}
	if len(rows) == 1 {
		query := prefix + strings.Join(stockQueryTerms(rows[0]), " ")
		if len(query) > maxChars {
			return strings.TrimSpace(query[:maxChars])
		}
		return query
	}
	return composeCompanyBatchQuery(prefix, rows, 8)
}

func splitRowsByQueryBudget(market string, rows []search.ScreeningSignalRow, maxChars int) [][]search.ScreeningSignalRow {
	var batches [][]search.ScreeningSignalRow
	var current []search.ScreeningSignalRow
	for _, row := range rows {
		candidate := append(append([]search.ScreeningSignalRow{}, current...), row)
		if len(current) > 0 && len(buildCompanyBatchQuery(market, candidate, maxChars)) > maxChars {
			batches = append(batches, current)
			current = []search.ScreeningSignalRow{row}
		} else {
			current = candidate
		}
	}
	if len(current) > 0 {
		batches = append(batches, current)
	}
	return batches
}

func composeCompanyBatchQuery(prefix string, rows []search.ScreeningSignalRow, nameMax int) string {
	parts := make([]string, 0, len(rows))
	for _, row := range rows {
		terms := stockQueryTerms(row)
		name := fitName(row.Name, nameMax)
		if name != "" && !contains(terms, name) {
			terms = append(terms, name)
		}
		parts = append(parts, strings.Join(terms, " "))
	}
	return prefix + strings.Join(parts, "; ")
}

func assignDocumentsToStocks(docs []search.SearchDocument, rows []search.ScreeningSignalRow) map[string][]search.SearchDocument {
	grouped := make(map[string][]search.SearchDocument, len(rows))
	for _, row := range rows {
		grouped[row.Code] = nil
	}
	for _, doc := range docs {
		for _, row := range rows {
			if documentMatchesStock(doc, row) {
				grouped[row.Code] = append(grouped[row.Code], doc)
			}
		}
	}
	return grouped
}

func documentMatchesStock(doc search.SearchDocument, row search.ScreeningSignalRow) bool {
	text := normalizeMatchText(doc.Title + " " + doc.Content + " " + doc.URL)
	for _, term := range stockIdentityTerms(row) {
		if containsTerm(text, normalizeMatchText(term)) {
			return true
		}
	}
	return false
}

func stockIdentityTerms(row search.ScreeningSignalRow) []string {
	terms := stockCodeAliasTerms(row)
	if row.Name != "" {
		terms = appendUnique(terms, row.Name)
	}
	ticker := normalizeTicker(row.Code)
	if isDigits(ticker) {
		noZero := strings.TrimLeft(ticker, "0")
		if len(noZero) >= 3 {
			terms = appendUnique(terms, noZero)
		}
	}
	return terms
}

func stockQueryTerms(row search.ScreeningSignalRow) []string {
	terms := []string{}
	code := strings.TrimSpace(row.Code)
	ticker := normalizeTicker(code)
	market := inferMarket(row, code)
	terms = appendUnique(terms, code)
	terms = appendUnique(terms, ticker)
	switch market {
	case "HK":
		for _, value := range hkTickerForms(ticker) {
			terms = appendUnique(terms, value+".HK")
		}
	case "US":
		for _, value := range usTickerForms(ticker) {
			terms = appendUnique(terms, value)
		}
	case "SH", "SZ", "BJ", "A":
		exchange := market
		if exchange == "A" {
			exchange = aShareExchange(code)
		}
		if exchange != "" && ticker != "" {
			terms = appendUnique(terms, ticker+"."+exchange)
		}
	}
	return terms
}

func stockCodeAliasTerms(row search.ScreeningSignalRow) []string {
	terms := []string{}
	code := strings.TrimSpace(row.Code)
	ticker := normalizeTicker(code)
	market := inferMarket(row, code)
	terms = appendUnique(terms, code)
	terms = appendUnique(terms, ticker)
	switch market {
	case "HK":
		for _, value := range hkTickerForms(ticker) {
			terms = appendUnique(terms, value+".HK")
			terms = appendUnique(terms, value+" HK")
			terms = appendUnique(terms, value+"-HK")
		}
	case "US":
		for _, value := range usTickerForms(ticker) {
			terms = appendUnique(terms, value)
			terms = appendUnique(terms, value+".US")
			terms = appendUnique(terms, value+" US")
		}
	case "SH", "SZ", "BJ", "A":
		exchange := market
		if exchange == "A" {
			exchange = aShareExchange(code)
		}
		if exchange != "" && ticker != "" {
			terms = appendUnique(terms, ticker+"."+exchange)
			terms = appendUnique(terms, exchange+ticker)
		}
	}
	return terms
}

func inferMarket(row search.ScreeningSignalRow, code string) string {
	value := strings.TrimSpace(code)
	if strings.Contains(value, ".") {
		parts := strings.SplitN(value, ".", 2)
		left, right := strings.ToUpper(parts[0]), strings.ToUpper(parts[1])
		if left == "HK" || left == "US" || left == "SH" || left == "SZ" || left == "BJ" {
			return left
		}
		if right == "HK" || right == "US" || right == "SH" || right == "SZ" || right == "SS" || right == "BJ" {
			if right == "SS" {
				return "SH"
			}
			return right
		}
	}
	market := strings.ToUpper(strings.TrimSpace(row.Market))
	if market == "A" {
		if exchange := aShareExchange(value); exchange != "" {
			return exchange
		}
	}
	return market
}

func normalizeTicker(code string) string {
	value := strings.TrimSpace(code)
	if strings.Contains(value, ".") {
		parts := strings.SplitN(value, ".", 2)
		left, right := strings.ToUpper(parts[0]), strings.ToUpper(parts[1])
		if left == "HK" || left == "US" || left == "SH" || left == "SZ" || left == "BJ" {
			return parts[1]
		}
		if right == "HK" || right == "US" || right == "SH" || right == "SZ" || right == "SS" || right == "BJ" {
			return parts[0]
		}
	}
	return value
}

func hkTickerForms(ticker string) []string {
	value := strings.TrimSpace(ticker)
	if value == "" {
		return nil
	}
	forms := []string{value}
	if isDigits(value) {
		if len(value) == 5 && strings.HasPrefix(value, "0") {
			forms = append(forms, value[len(value)-4:])
		}
		forms = append(forms, leftPad(value, 5), leftPad(value, 4))
		if noZero := strings.TrimLeft(value, "0"); len(noZero) >= 3 {
			forms = append(forms, noZero)
		}
	}
	return dedupeStrings(forms)
}

func usTickerForms(ticker string) []string {
	value := strings.ToUpper(strings.TrimSpace(ticker))
	forms := []string{value}
	if strings.Contains(value, "-") {
		forms = append(forms, strings.ReplaceAll(value, "-", "."), strings.ReplaceAll(value, "-", " "))
	}
	if strings.Contains(value, ".") {
		forms = append(forms, strings.ReplaceAll(value, ".", "-"), strings.ReplaceAll(value, ".", " "))
	}
	return dedupeStrings(forms)
}

func aShareExchange(code string) string {
	ticker := normalizeTicker(code)
	if len(ticker) < 6 || !isDigits(ticker) {
		return ""
	}
	switch {
	case strings.HasPrefix(ticker, "60"), strings.HasPrefix(ticker, "68"), strings.HasPrefix(ticker, "90"):
		return "SH"
	case strings.HasPrefix(ticker, "00"), strings.HasPrefix(ticker, "30"), strings.HasPrefix(ticker, "20"):
		return "SZ"
	case strings.HasPrefix(ticker, "43"), strings.HasPrefix(ticker, "83"), strings.HasPrefix(ticker, "87"), strings.HasPrefix(ticker, "88"):
		return "BJ"
	default:
		return ""
	}
}

func normalizeMatchText(value string) string {
	return strings.Join(strings.Fields(strings.ToLower(value)), " ")
}

func containsTerm(text, term string) bool {
	if term == "" {
		return false
	}
	if regexp.MustCompile(`^[a-z0-9]+$`).MatchString(term) {
		return regexp.MustCompile(`(^|[^a-z0-9])`+regexp.QuoteMeta(term)+`([^a-z0-9]|$)`).FindStringIndex(text) != nil
	}
	return strings.Contains(text, term)
}

func fitName(name string, budget int) string {
	value := strings.Join(strings.Fields(name), " ")
	if budget <= 0 || value == "" {
		return ""
	}
	if len(value) <= budget {
		return value
	}
	return strings.TrimSpace(value[:budget])
}

func dedupeDocuments(docs []search.SearchDocument) []search.SearchDocument {
	seen := map[string]bool{}
	result := make([]search.SearchDocument, 0, len(docs))
	for _, doc := range docs {
		key := doc.URL + "\x00" + doc.Title + "\x00" + doc.Content
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, doc)
	}
	return result
}

func dedupeStrings(values []string) []string {
	var result []string
	for _, value := range values {
		result = appendUnique(result, value)
	}
	return result
}

func appendUnique(values []string, value string) []string {
	value = strings.TrimSpace(value)
	if value == "" || contains(values, value) {
		return values
	}
	return append(values, value)
}

func contains(values []string, value string) bool {
	for _, existing := range values {
		if existing == value {
			return true
		}
	}
	return false
}

func isDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, ch := range value {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func leftPad(value string, width int) string {
	for len(value) < width {
		value = "0" + value
	}
	return value
}
