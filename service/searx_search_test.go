package service

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/require"
)

func TestExecuteSearxStandaloneSearchReturnsCodexCompatibleResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/search", r.URL.Path)
		require.Equal(t, "json", r.URL.Query().Get("format"))
		require.Contains(t, r.URL.Query().Get("q"), "XT desktop agent")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[
			{"url":"https://example.com/a","title":"Result A","content":"First result","engines":["bing"]},
			{"url":"https://example.com/b","title":"Result B","content":"Second result","engine":"baidu"}
		]}`))
	}))
	defer server.Close()

	request := &dto.SearchRequest{
		ID:       "search_1",
		Model:    "gpt-5.6-sol",
		Commands: []byte(`{"search_query":[{"q":"XT desktop agent"}],"response_length":"short"}`),
	}
	response, err := ExecuteSearxStandaloneSearch(context.Background(), server.URL, request)
	require.NoError(t, err)
	require.Contains(t, response.Output, `Search results for "XT desktop agent"`)
	require.Contains(t, response.Output, "xtsearch_")
	require.Contains(t, response.Output, "https://example.com/a")
	require.Len(t, response.Results, 2)
}

func TestExecuteSearxStandaloneSearchAppliesDomainAndLengthFilters(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Contains(t, r.URL.Query().Get("q"), "site:allowed.example")
		_, _ = w.Write([]byte(`{"results":[
			{"url":"https://allowed.example/1","title":"One","content":"one"},
			{"url":"https://blocked.example/2","title":"Two","content":"two"},
			{"url":"https://allowed.example/3","title":"Three","content":"three"},
			{"url":"https://allowed.example/4","title":"Four","content":"four"},
			{"url":"https://allowed.example/5","title":"Five","content":"five"},
			{"url":"https://allowed.example/6","title":"Six","content":"six"},
			{"url":"javascript:alert(1)","title":"Unsafe","content":"unsafe"}
		]}`))
	}))
	defer server.Close()

	request := &dto.SearchRequest{ID: "search_2", Model: "gpt-5.6-sol", Commands: []byte(`{
		"search_query":[{"q":"policy","domains":["allowed.example"]}],
		"response_length":"short"
	}`), Settings: []byte(`{"filters":{"blocked_domains":["blocked.example"]}}`)}
	response, err := ExecuteSearxStandaloneSearch(context.Background(), server.URL, request)
	require.NoError(t, err)
	require.Len(t, response.Results, 5)
	require.NotContains(t, response.Output, "blocked.example")
}

func TestValidSearchResultURLAcceptsOnlyHTTPAndHTTPS(t *testing.T) {
	require.True(t, validSearchResultURL("https://example.com/path"))
	require.True(t, validSearchResultURL("http://example.com/path"))
	require.False(t, validSearchResultURL("javascript:alert(1)"))
	require.False(t, validSearchResultURL("file:///etc/passwd"))
	require.False(t, validSearchResultURL("not-a-url"))
}

func TestExecuteSearxStandaloneSearchRejectsInvalidOrEmptyCommands(t *testing.T) {
	_, err := ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{})
	require.EqualError(t, err, "search commands are required")

	_, err = ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{Commands: []byte(`{"unknown":true}`)})
	require.EqualError(t, err, "search request contains no supported command")
}

func TestExecuteSearxStandaloneSearchBoundsCommandVolumeAndQueryLength(t *testing.T) {
	tooManyQueries := `{"search_query":[{"q":"1"},{"q":"2"},{"q":"3"},{"q":"4"},{"q":"5"}]}`
	_, err := ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{Commands: []byte(tooManyQueries)})
	require.EqualError(t, err, "search_query and image_query each support at most 4 entries")

	longQuery := strings.Repeat("中", maxSearchQueryRunes+1)
	_, err = ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{
		Commands: []byte(`{"search_query":[{"q":"` + longQuery + `"}]}`),
	})
	require.EqualError(t, err, "search query exceeds 2000 characters")
}

func TestExecuteSearxStandaloneSearchRejectsBackendFailureAndInvalidJSON(t *testing.T) {
	for _, handler := range []http.HandlerFunc{
		func(w http.ResponseWriter, _ *http.Request) { http.Error(w, "failed", http.StatusBadGateway) },
		func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("not-json")) },
	} {
		server := httptest.NewServer(handler)
		request := &dto.SearchRequest{Commands: []byte(`{"search_query":[{"q":"test"}]}`)}
		_, err := ExecuteSearxStandaloneSearch(context.Background(), server.URL, request)
		require.Error(t, err)
		server.Close()
	}
}

func TestExecuteSearxStandaloneSearchTimeCommand(t *testing.T) {
	response, err := ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{
		Commands: []byte(`{"time":[{"utc_offset":"+08:00"}]}`),
	})
	require.NoError(t, err)
	require.Contains(t, response.Output, "UTC+08:00")
	require.Len(t, response.Results, 1)
}

func TestExecuteSearxStandaloneSearchHonorsDisabledExternalWebAccess(t *testing.T) {
	_, err := ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{
		Commands: []byte(`{"search_query":[{"q":"test"}]}`),
		Settings: []byte(`{"external_web_access":false}`),
	})
	require.EqualError(t, err, "external web access is disabled for this search request")

	response, err := ExecuteSearxStandaloneSearch(context.Background(), "http://search.internal", &dto.SearchRequest{
		Commands: []byte(`{"time":[{"utc_offset":"+08:00"}]}`),
		Settings: []byte(`{"external_web_access":false}`),
	})
	require.NoError(t, err)
	require.Contains(t, response.Output, "UTC+08:00")
}

func TestValidatePublicHTTPURLRejectsSSRFAddresses(t *testing.T) {
	for _, rawURL := range []string{
		"http://127.0.0.1/admin",
		"http://[::1]/admin",
		"http://169.254.169.254/latest/meta-data",
		"file:///etc/passwd",
		"http://user:password@example.com/",
	} {
		_, err := validatePublicHTTPURL(context.Background(), rawURL)
		require.Error(t, err, rawURL)
	}
}

func TestExtractHTMLDocumentDropsExecutableContentAndCollectsLinks(t *testing.T) {
	base, err := url.Parse("https://example.com/root/")
	require.NoError(t, err)
	title, text, links := extractHTMLDocument([]byte(`
		<html><head><title>Example title</title><style>secret-style</style></head>
		<body><script>secret-script</script><h1>Hello</h1><a href="next">Next page</a></body></html>
	`), base, "")
	require.Equal(t, "Example title", title)
	require.Contains(t, text, "Hello")
	require.NotContains(t, text, "secret-script")
	require.NotContains(t, text, "secret-style")
	require.Equal(t, []cachedSearchLink{{Text: "Next page", URL: "https://example.com/root/next"}}, links)
}

func TestReadBoundedBodyRejectsOversizedResponse(t *testing.T) {
	_, err := readBoundedBody(strings.NewReader("123456"), 5)
	require.EqualError(t, err, "response exceeds 5 bytes")
}
