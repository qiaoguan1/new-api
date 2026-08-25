package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"golang.org/x/net/html"
)

const (
	searchRequestTimeout  = 35 * time.Second
	openRequestTimeout    = 25 * time.Second
	maxSearchResponseSize = 4 << 20
	maxOpenResponseSize   = 3 << 20
	maxOpenTextRunes      = 24_000
	searchCacheTTL        = 30 * time.Minute
	maxRedirects          = 5
	maxSearchQueries      = 4
	maxSearchOperations   = 24
	maxSearchQueryRunes   = 2_000
	maxSearchCacheEntries = 10_000
)

type standaloneSearchCommands struct {
	SearchQuery    []standaloneSearchQuery `json:"search_query"`
	ImageQuery     []standaloneSearchQuery `json:"image_query"`
	Open           []standaloneOpen        `json:"open"`
	Click          []standaloneClick       `json:"click"`
	Find           []standaloneFind        `json:"find"`
	Screenshot     []json.RawMessage       `json:"screenshot"`
	Finance        []json.RawMessage       `json:"finance"`
	Weather        []json.RawMessage       `json:"weather"`
	Sports         []json.RawMessage       `json:"sports"`
	Time           []standaloneTime        `json:"time"`
	ResponseLength string                  `json:"response_length"`
}

type standaloneSearchQuery struct {
	Query   string   `json:"q"`
	Recency *uint64  `json:"recency"`
	Domains []string `json:"domains"`
}

type standaloneOpen struct {
	RefID  string  `json:"ref_id"`
	LineNo *uint64 `json:"lineno"`
}

type standaloneClick struct {
	RefID string `json:"ref_id"`
	ID    uint64 `json:"id"`
}

type standaloneFind struct {
	RefID   string `json:"ref_id"`
	Pattern string `json:"pattern"`
}

type standaloneTime struct {
	UTCOffset string `json:"utc_offset"`
}

type searxResponse struct {
	Results []searxResult `json:"results"`
}

type searxResult struct {
	URL       string   `json:"url"`
	Title     string   `json:"title"`
	Content   string   `json:"content"`
	Engine    string   `json:"engine"`
	Engines   []string `json:"engines"`
	ImageSrc  string   `json:"img_src"`
	Thumbnail string   `json:"thumbnail_src"`
}

type cachedSearchDocument struct {
	URL       string
	Title     string
	Text      string
	Links     []cachedSearchLink
	ExpiresAt time.Time
}

type cachedSearchLink struct {
	Text string
	URL  string
}

var standaloneSearchCache = struct {
	sync.Mutex
	entries map[string]cachedSearchDocument
}{entries: make(map[string]cachedSearchDocument)}

// ExecuteSearxStandaloneSearch translates the official Codex standalone search
// command envelope into SearXNG queries and safe page reads. Search content is
// returned as plain text and opaque structured results; it is never executed.
func ExecuteSearxStandaloneSearch(ctx context.Context, baseURL string, request *dto.SearchRequest) (*dto.SearchResponse, error) {
	if request == nil {
		return nil, errors.New("search request is required")
	}
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if baseURL == "" {
		return nil, errors.New("search backend URL is required")
	}
	parsedBase, err := url.Parse(baseURL)
	if err != nil || (parsedBase.Scheme != "http" && parsedBase.Scheme != "https") || parsedBase.Host == "" {
		return nil, errors.New("search backend URL is invalid")
	}

	var commands standaloneSearchCommands
	if len(request.Commands) == 0 {
		return nil, errors.New("search commands are required")
	}
	if err := common.Unmarshal(request.Commands, &commands); err != nil {
		return nil, fmt.Errorf("invalid search commands: %w", err)
	}
	if err := validateStandaloneCommands(commands); err != nil {
		return nil, err
	}
	if !request.ExternalWebAccessEnabled() && standaloneCommandsNeedNetwork(commands) {
		return nil, errors.New("external web access is disabled for this search request")
	}

	limit := searchResultLimit(commands.ResponseLength)
	allowedDomains, blockedDomains := request.SearchDomainFilters()
	sections := make([]string, 0, 8)
	structured := make([]json.RawMessage, 0, 16)

	for _, query := range commands.SearchQuery {
		if len(query.Domains) == 0 {
			query.Domains = allowedDomains
		}
		section, results, err := executeSearxQuery(ctx, baseURL, query, blockedDomains, false, limit)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, results...)
	}
	for _, query := range commands.ImageQuery {
		if len(query.Domains) == 0 {
			query.Domains = allowedDomains
		}
		section, results, err := executeSearxQuery(ctx, baseURL, query, blockedDomains, true, limit)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, results...)
	}
	for _, operation := range commands.Open {
		section, result, err := executeOpenOperation(ctx, operation.RefID, operation.LineNo)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, result)
	}
	for _, operation := range commands.Click {
		section, result, err := executeClickOperation(ctx, operation)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, result)
	}
	for _, operation := range commands.Find {
		section, result, err := executeFindOperation(ctx, operation)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, result)
	}
	for _, operation := range commands.Time {
		section, result, err := executeTimeOperation(operation)
		if err != nil {
			return nil, err
		}
		sections = append(sections, section)
		structured = append(structured, result)
	}

	for _, group := range []struct {
		name  string
		items []json.RawMessage
	}{
		{name: "finance", items: commands.Finance},
		{name: "weather", items: commands.Weather},
		{name: "sports", items: commands.Sports},
	} {
		for _, item := range group.items {
			query := standaloneSearchQuery{Query: group.name + " " + compactJSONText(item), Domains: allowedDomains}
			section, results, err := executeSearxQuery(ctx, baseURL, query, blockedDomains, false, limit)
			if err != nil {
				return nil, err
			}
			sections = append(sections, section)
			structured = append(structured, results...)
		}
	}
	if len(commands.Screenshot) > 0 {
		sections = append(sections, "PDF screenshots are not available from the XT search backend. Open the PDF URL to inspect its text content.")
	}

	if len(sections) == 0 {
		return nil, errors.New("search request contains no supported command")
	}
	return &dto.SearchResponse{
		Output:  strings.Join(sections, "\n\n"),
		Results: structured,
	}, nil
}

func standaloneCommandsNeedNetwork(commands standaloneSearchCommands) bool {
	return len(commands.SearchQuery)+len(commands.ImageQuery)+len(commands.Open)+len(commands.Click)+
		len(commands.Find)+len(commands.Screenshot)+len(commands.Finance)+len(commands.Weather)+len(commands.Sports) > 0
}

func validateStandaloneCommands(commands standaloneSearchCommands) error {
	if len(commands.SearchQuery) > maxSearchQueries || len(commands.ImageQuery) > maxSearchQueries {
		return fmt.Errorf("search_query and image_query each support at most %d entries", maxSearchQueries)
	}
	total := len(commands.SearchQuery) + len(commands.ImageQuery) + len(commands.Open) + len(commands.Click) +
		len(commands.Find) + len(commands.Screenshot) + len(commands.Finance) + len(commands.Weather) +
		len(commands.Sports) + len(commands.Time)
	if total > maxSearchOperations {
		return fmt.Errorf("search request supports at most %d operations", maxSearchOperations)
	}
	for _, query := range append(append([]standaloneSearchQuery(nil), commands.SearchQuery...), commands.ImageQuery...) {
		if len([]rune(query.Query)) > maxSearchQueryRunes {
			return fmt.Errorf("search query exceeds %d characters", maxSearchQueryRunes)
		}
	}
	return nil
}

func searchResultLimit(responseLength string) int {
	switch strings.ToLower(strings.TrimSpace(responseLength)) {
	case "short":
		return 5
	case "long":
		return 15
	default:
		return 10
	}
}

func executeSearxQuery(ctx context.Context, baseURL string, query standaloneSearchQuery, blockedDomains []string, images bool, limit int) (string, []json.RawMessage, error) {
	query.Query = strings.TrimSpace(query.Query)
	if query.Query == "" {
		return "", nil, errors.New("search query is empty")
	}
	values := url.Values{}
	values.Set("q", applyDomainFilter(query.Query, query.Domains))
	values.Set("format", "json")
	values.Set("language", "auto")
	if images {
		values.Set("categories", "images")
	}
	if timeRange := searxTimeRange(query.Recency); timeRange != "" {
		values.Set("time_range", timeRange)
	}
	requestURL := baseURL + "/search?" + values.Encode()
	requestCtx, cancel := context.WithTimeout(ctx, searchRequestTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(requestCtx, http.MethodGet, requestURL, nil)
	if err != nil {
		return "", nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "XT-Search/1.0")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", nil, fmt.Errorf("search backend request failed: %w", err)
	}
	defer CloseResponseBodyGracefully(resp)
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		return "", nil, fmt.Errorf("search backend returned HTTP %d", resp.StatusCode)
	}
	body, err := readBoundedBody(resp.Body, maxSearchResponseSize)
	if err != nil {
		return "", nil, fmt.Errorf("search backend response failed: %w", err)
	}
	var payload searxResponse
	if err := common.Unmarshal(body, &payload); err != nil {
		return "", nil, fmt.Errorf("search backend returned invalid JSON: %w", err)
	}

	lines := []string{fmt.Sprintf("Search results for %q:", query.Query)}
	structured := make([]json.RawMessage, 0, limit)
	count := 0
	for _, result := range payload.Results {
		if count >= limit {
			break
		}
		result.URL = strings.TrimSpace(result.URL)
		if !validSearchResultURL(result.URL) || domainBlocked(result.URL, query.Domains, blockedDomains) {
			continue
		}
		refID := cacheSearchReference(result.URL, result.Title)
		engine := result.Engine
		if engine == "" && len(result.Engines) > 0 {
			engine = strings.Join(result.Engines, ",")
		}
		lines = append(lines,
			fmt.Sprintf("[%s] %s", refID, cleanSearchText(result.Title, 240)),
			"URL: "+result.URL,
			"Snippet: "+cleanSearchText(result.Content, 700),
		)
		item := map[string]any{
			"type":    map[bool]string{true: "image_result", false: "search_result"}[images],
			"ref_id":  refID,
			"title":   result.Title,
			"url":     result.URL,
			"snippet": result.Content,
			"engine":  engine,
		}
		if images {
			item["image_url"] = firstNonEmpty(result.ImageSrc, result.Thumbnail)
		}
		encoded, _ := common.Marshal(item)
		structured = append(structured, encoded)
		count++
	}
	if count == 0 {
		lines = append(lines, "No results found.")
	}
	return strings.Join(lines, "\n"), structured, nil
}

func executeOpenOperation(ctx context.Context, refID string, lineNo *uint64) (string, json.RawMessage, error) {
	refID = strings.TrimSpace(refID)
	if refID == "" {
		return "", nil, errors.New("open ref_id is required")
	}
	document, ok := loadSearchReference(refID)
	if !ok {
		document = cachedSearchDocument{URL: refID}
	}
	document, err := fetchSearchDocument(ctx, document.URL, document.Title)
	if err != nil {
		return "", nil, err
	}
	pageRef := cacheSearchDocument(document)
	start := 0
	if lineNo != nil {
		start = int(*lineNo)
	}
	lines := numberSearchLines(document.Text)
	if start < 0 || start >= len(lines) {
		start = 0
	}
	end := start + 120
	if end > len(lines) {
		end = len(lines)
	}
	output := []string{
		fmt.Sprintf("Opened [%s] %s", pageRef, document.Title),
		"URL: " + document.URL,
	}
	output = append(output, lines[start:end]...)
	if len(document.Links) > 0 {
		output = append(output, "Links:")
		for index, link := range document.Links {
			if index >= 30 {
				break
			}
			output = append(output, fmt.Sprintf("%d: %s — %s", index, cleanSearchText(link.Text, 180), link.URL))
		}
	}
	encoded, _ := common.Marshal(map[string]any{
		"type":   "open_result",
		"ref_id": pageRef,
		"title":  document.Title,
		"url":    document.URL,
		"text":   document.Text,
	})
	return strings.Join(output, "\n"), encoded, nil
}

func executeClickOperation(ctx context.Context, operation standaloneClick) (string, json.RawMessage, error) {
	document, ok := loadSearchReference(strings.TrimSpace(operation.RefID))
	if !ok || len(document.Links) == 0 {
		return "", nil, fmt.Errorf("click ref_id %q has no cached links", operation.RefID)
	}
	if operation.ID >= uint64(len(document.Links)) {
		return "", nil, fmt.Errorf("click id %d is out of range", operation.ID)
	}
	target := document.Links[operation.ID]
	return executeOpenOperation(ctx, target.URL, nil)
}

func executeFindOperation(ctx context.Context, operation standaloneFind) (string, json.RawMessage, error) {
	pattern := strings.TrimSpace(operation.Pattern)
	if pattern == "" {
		return "", nil, errors.New("find pattern is required")
	}
	document, ok := loadSearchReference(strings.TrimSpace(operation.RefID))
	if !ok || document.Text == "" {
		target := operation.RefID
		if ok && document.URL != "" {
			target = document.URL
		}
		fetched, err := fetchSearchDocument(ctx, target, document.Title)
		if err != nil {
			return "", nil, err
		}
		document = fetched
		cacheSearchDocument(document)
	}
	needle := strings.ToLower(pattern)
	matches := make([]string, 0, 20)
	for index, line := range strings.Split(document.Text, "\n") {
		if strings.Contains(strings.ToLower(line), needle) {
			matches = append(matches, fmt.Sprintf("L%d: %s", index, line))
			if len(matches) >= 20 {
				break
			}
		}
	}
	if len(matches) == 0 {
		matches = append(matches, "No matching text found.")
	}
	encoded, _ := common.Marshal(map[string]any{
		"type":    "find_result",
		"ref_id":  operation.RefID,
		"pattern": pattern,
		"matches": matches,
	})
	return fmt.Sprintf("Find %q in %s:\n%s", pattern, document.URL, strings.Join(matches, "\n")), encoded, nil
}

func executeTimeOperation(operation standaloneTime) (string, json.RawMessage, error) {
	offset := strings.TrimSpace(operation.UTCOffset)
	if len(offset) != 6 || (offset[0] != '+' && offset[0] != '-') || offset[3] != ':' {
		return "", nil, fmt.Errorf("invalid UTC offset %q", offset)
	}
	hours, errHour := strconv.Atoi(offset[1:3])
	minutes, errMinute := strconv.Atoi(offset[4:6])
	if errHour != nil || errMinute != nil || hours > 14 || minutes > 59 || (hours == 14 && minutes != 0) {
		return "", nil, fmt.Errorf("invalid UTC offset %q", offset)
	}
	seconds := hours*3600 + minutes*60
	if offset[0] == '-' {
		seconds = -seconds
	}
	value := time.Now().In(time.FixedZone("UTC"+offset, seconds)).Format(time.RFC3339)
	encoded, _ := common.Marshal(map[string]any{"type": "time_result", "utc_offset": offset, "time": value})
	return "Current time at UTC" + offset + ": " + value, encoded, nil
}

func fetchSearchDocument(ctx context.Context, rawURL string, fallbackTitle string) (cachedSearchDocument, error) {
	parsed, err := validatePublicHTTPURL(ctx, rawURL)
	if err != nil {
		return cachedSearchDocument{}, err
	}
	requestCtx, cancel := context.WithTimeout(ctx, openRequestTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(requestCtx, http.MethodGet, parsed.String(), nil)
	if err != nil {
		return cachedSearchDocument{}, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; XT-Search/1.0)")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1")
	client := safeExternalHTTPClient()
	resp, err := client.Do(req)
	if err != nil {
		return cachedSearchDocument{}, fmt.Errorf("open request failed: %w", err)
	}
	defer CloseResponseBodyGracefully(resp)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		return cachedSearchDocument{}, fmt.Errorf("open request returned HTTP %d", resp.StatusCode)
	}
	body, err := readBoundedBody(resp.Body, maxOpenResponseSize)
	if err != nil {
		return cachedSearchDocument{}, err
	}
	contentType := strings.ToLower(resp.Header.Get("Content-Type"))
	text := string(body)
	title := fallbackTitle
	links := []cachedSearchLink(nil)
	if strings.Contains(contentType, "html") || strings.Contains(strings.ToLower(text[:searchMinInt(len(text), 256)]), "<html") {
		title, text, links = extractHTMLDocument(body, parsed, fallbackTitle)
	} else {
		text = cleanSearchText(text, maxOpenTextRunes)
	}
	return cachedSearchDocument{URL: parsed.String(), Title: title, Text: text, Links: links, ExpiresAt: time.Now().Add(searchCacheTTL)}, nil
}

func extractHTMLDocument(body []byte, base *url.URL, fallbackTitle string) (string, string, []cachedSearchLink) {
	document, err := html.Parse(strings.NewReader(string(body)))
	if err != nil {
		return fallbackTitle, cleanSearchText(string(body), maxOpenTextRunes), nil
	}
	title := fallbackTitle
	textParts := make([]string, 0, 1024)
	links := make([]cachedSearchLink, 0, 64)
	var walk func(*html.Node, bool)
	walk = func(node *html.Node, skipped bool) {
		if node.Type == html.ElementNode {
			switch strings.ToLower(node.Data) {
			case "script", "style", "noscript", "svg", "canvas":
				skipped = true
			case "title":
				if node.FirstChild != nil && node.FirstChild.Type == html.TextNode && title == "" {
					title = cleanSearchText(node.FirstChild.Data, 240)
				}
			case "a":
				if target := htmlAttribute(node, "href"); target != "" {
					if resolved, err := base.Parse(target); err == nil && (resolved.Scheme == "http" || resolved.Scheme == "https") {
						links = append(links, cachedSearchLink{Text: nodeText(node), URL: resolved.String()})
					}
				}
			}
		}
		if !skipped && node.Type == html.TextNode {
			if value := cleanSearchText(node.Data, 1000); value != "" {
				textParts = append(textParts, value)
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child, skipped)
		}
	}
	walk(document, false)
	text := cleanSearchText(strings.Join(textParts, "\n"), maxOpenTextRunes)
	return title, text, deduplicateSearchLinks(links)
}

func safeExternalHTTPClient() *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, err
		}
		ips, err := net.DefaultResolver.LookupIP(ctx, "ip", host)
		if err != nil {
			return nil, err
		}
		for _, ip := range ips {
			if isPublicSearchIP(ip) {
				return dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
			}
		}
		return nil, fmt.Errorf("open target %q resolves only to non-public addresses", host)
	}
	return &http.Client{
		Timeout:   openRequestTimeout,
		Transport: transport,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= maxRedirects {
				return errors.New("too many redirects")
			}
			_, err := validatePublicHTTPURL(req.Context(), req.URL.String())
			return err
		},
	}
}

func validatePublicHTTPURL(ctx context.Context, rawURL string) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, errors.New("open target must be a public HTTP or HTTPS URL")
	}
	if parsed.User != nil {
		return nil, errors.New("open target must not contain URL credentials")
	}
	ips, err := net.DefaultResolver.LookupIP(ctx, "ip", parsed.Hostname())
	if err != nil {
		return nil, fmt.Errorf("open target cannot be resolved: %w", err)
	}
	for _, ip := range ips {
		if !isPublicSearchIP(ip) {
			return nil, fmt.Errorf("open target resolves to a non-public address")
		}
	}
	return parsed, nil
}

func isPublicSearchIP(ip net.IP) bool {
	return ip != nil && !ip.IsLoopback() && !ip.IsPrivate() && !ip.IsUnspecified() &&
		!ip.IsLinkLocalUnicast() && !ip.IsLinkLocalMulticast() && !ip.IsMulticast()
}

func readBoundedBody(reader io.Reader, limit int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > limit {
		return nil, fmt.Errorf("response exceeds %d bytes", limit)
	}
	return body, nil
}

func cacheSearchReference(rawURL string, title string) string {
	refID := "xtsearch_" + shortSearchHash(rawURL)
	standaloneSearchCache.Lock()
	pruneSearchCacheLocked(time.Now())
	standaloneSearchCache.entries[refID] = cachedSearchDocument{URL: rawURL, Title: title, ExpiresAt: time.Now().Add(searchCacheTTL)}
	standaloneSearchCache.Unlock()
	return refID
}

func cacheSearchDocument(document cachedSearchDocument) string {
	refID := "xtopen_" + shortSearchHash(document.URL)
	if document.ExpiresAt.IsZero() {
		document.ExpiresAt = time.Now().Add(searchCacheTTL)
	}
	standaloneSearchCache.Lock()
	pruneSearchCacheLocked(time.Now())
	standaloneSearchCache.entries[refID] = document
	standaloneSearchCache.Unlock()
	return refID
}

func loadSearchReference(refID string) (cachedSearchDocument, bool) {
	standaloneSearchCache.Lock()
	defer standaloneSearchCache.Unlock()
	pruneSearchCacheLocked(time.Now())
	document, ok := standaloneSearchCache.entries[refID]
	return document, ok
}

func pruneSearchCacheLocked(now time.Time) {
	for refID, document := range standaloneSearchCache.entries {
		if !document.ExpiresAt.After(now) {
			delete(standaloneSearchCache.entries, refID)
		}
	}
	for len(standaloneSearchCache.entries) >= maxSearchCacheEntries {
		for refID := range standaloneSearchCache.entries {
			delete(standaloneSearchCache.entries, refID)
			break
		}
	}
}

func shortSearchHash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:8])
}

func searxTimeRange(recency *uint64) string {
	if recency == nil || *recency == 0 {
		return ""
	}
	switch {
	case *recency <= 1:
		return "day"
	case *recency <= 7:
		return "week"
	case *recency <= 31:
		return "month"
	default:
		return "year"
	}
}

func applyDomainFilter(query string, domains []string) string {
	valid := make([]string, 0, len(domains))
	for _, domain := range domains {
		domain = strings.TrimSpace(strings.ToLower(domain))
		if domain != "" && !strings.ContainsAny(domain, " /\\") {
			valid = append(valid, "site:"+domain)
		}
	}
	if len(valid) == 0 {
		return query
	}
	return query + " (" + strings.Join(valid, " OR ") + ")"
}

func domainBlocked(rawURL string, allowed []string, blocked []string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return true
	}
	host := strings.ToLower(parsed.Hostname())
	for _, domain := range blocked {
		domain = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(domain)), ".")
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return true
		}
	}
	if len(allowed) == 0 {
		return false
	}
	for _, domain := range allowed {
		domain = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(domain)), ".")
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return false
		}
	}
	return true
}

func validSearchResultURL(rawURL string) bool {
	parsed, err := url.Parse(strings.TrimSpace(rawURL))
	return err == nil && parsed.Hostname() != "" && (parsed.Scheme == "http" || parsed.Scheme == "https")
}

func cleanSearchText(value string, maxRunes int) string {
	value = strings.ReplaceAll(value, "\r", "\n")
	lines := strings.Split(value, "\n")
	cleaned := make([]string, 0, len(lines))
	for _, line := range lines {
		line = strings.Join(strings.Fields(line), " ")
		if line != "" {
			cleaned = append(cleaned, line)
		}
	}
	value = strings.Join(cleaned, "\n")
	runes := []rune(value)
	if maxRunes > 0 && len(runes) > maxRunes {
		return string(runes[:maxRunes]) + "…"
	}
	return value
}

func numberSearchLines(text string) []string {
	parts := strings.Split(text, "\n")
	lines := make([]string, 0, len(parts))
	for index, part := range parts {
		lines = append(lines, fmt.Sprintf("L%d: %s", index, part))
	}
	return lines
}

func htmlAttribute(node *html.Node, name string) string {
	for _, attribute := range node.Attr {
		if strings.EqualFold(attribute.Key, name) {
			return strings.TrimSpace(attribute.Val)
		}
	}
	return ""
}

func nodeText(node *html.Node) string {
	parts := make([]string, 0, 4)
	var walk func(*html.Node)
	walk = func(current *html.Node) {
		if current.Type == html.TextNode {
			parts = append(parts, current.Data)
		}
		for child := current.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	return cleanSearchText(strings.Join(parts, " "), 240)
}

func deduplicateSearchLinks(links []cachedSearchLink) []cachedSearchLink {
	seen := make(map[string]struct{}, len(links))
	result := make([]cachedSearchLink, 0, len(links))
	for _, link := range links {
		if _, ok := seen[link.URL]; ok {
			continue
		}
		seen[link.URL] = struct{}{}
		result = append(result, link)
	}
	return result
}

func compactJSONText(raw json.RawMessage) string {
	return strings.Join(strings.Fields(string(raw)), " ")
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func searchMinInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}
