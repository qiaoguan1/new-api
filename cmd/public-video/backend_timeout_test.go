package main

import (
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type deadlineTransport func(*http.Request) (*http.Response, error)

func (transport deadlineTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func TestBackendMediaVerificationBudgetDoesNotMutateSharedClientOrOrdinaryCalls(t *testing.T) {
	for _, test := range []struct {
		name, method, path, body string
		budget                   time.Duration
	}{
		{"ordinary_get", "GET", "/v1/video-prices", "", 10 * time.Second},
		{"preflight_text", "POST", "/v1/operations/video-input-validation", `{"model":"wan3.0-video","mode":"text"}`, 75 * time.Second},
		{"plain_video_text", "POST", "/v1/videos", `{"model":"wan3.0-video","prompt":"A ball"}`, 10 * time.Second},
		{"generated_audio_is_not_an_asset", "POST", "/v1/videos", `{"prompt":"A ball","audio":true,"generate_audio":true}`, 10 * time.Second},
		{"video_images", "POST", "/v1/videos", `{"images":["https://media.example/a.png"]}`, 75 * time.Second},
		{"canonical_images", "POST", "/v1/videos", `{"reference_images":[{"url":"https://media.example/a.png"}]}`, 75 * time.Second},
		{"video_reference", "POST", "/v1/videos", `{"reference_videos":[{"url":"https://media.example/a.mp4"}]}`, 75 * time.Second},
		{"audio_reference", "POST", "/v1/videos", `{"reference_audios":[{"url":"https://media.example/a.mp3"}]}`, 75 * time.Second},
		{"single_video_reference", "POST", "/v1/videos", `{"video":{"url":"https://media.example/a.mp4"}}`, 75 * time.Second},
		{"single_audio_reference", "POST", "/v1/videos", `{"audio":"https://media.example/a.mp3"}`, 75 * time.Second},
		{"empty_assets", "POST", "/v1/videos", `{"images":[],"reference_videos":[],"reference_audios":[]}`, 10 * time.Second},
		{"unrelated_post", "POST", "/other-operation", `{"images":["https://media.example/a.png"]}`, 10 * time.Second},
	} {
		t.Run(test.name, func(t *testing.T) {
			client := &http.Client{Timeout: 60 * time.Second}
			client.Transport = deadlineTransport(func(request *http.Request) (*http.Response, error) {
				deadline, ok := request.Context().Deadline()
				require.True(t, ok)
				// Inspect the required deadline itself, not elapsed performance or
				// a sleeping server. A 60s shared client would shorten the 75s route.
				assert.InDelta(t, test.budget.Seconds(), time.Until(deadline).Seconds(), 1)
				assert.Equal(t, 60*time.Second, client.Timeout, "per-call copies must not mutate concurrent ordinary requests")
				return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{}`)), Request: request}, nil
			})
			s := &server{backend: "https://gateway.example", serviceToken: strings.Repeat("x", 32), client: client}
			_, status, err := s.backendJSON(test.method, test.path, []byte(test.body))
			require.NoError(t, err)
			assert.Equal(t, 200, status)
			assert.Equal(t, 60*time.Second, client.Timeout)
		})
	}
}
