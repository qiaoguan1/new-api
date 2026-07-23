package topaz

import (
	"bytes"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func TestFilterUpscaleModelsPreservesUpstreamOrder(t *testing.T) {
	models := filterUpscaleModels([]string{
		"apo-8",
		"slp-2.5",
		"prob-4",
		"color-1",
		"prob-4",
		"pnat-1",
		"nyx-3",
	})

	require.Equal(t, []string{"slp-2.5", "prob-4", "pnat-1"}, models)
}

func TestFetchUpscaleModelsUsesTopazAuthentication(t *testing.T) {
	var receivedAPIKey string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/video/status", r.URL.Path)
		receivedAPIKey = r.Header.Get("X-API-Key")
		require.Empty(t, r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"isAvailable":true,"supportedModels":["apo-8","rhea-1","slf-2","hyp-2"]}`)
	}))
	defer server.Close()

	models, err := FetchUpscaleModels(server.URL, "topaz-secret", "")

	require.NoError(t, err)
	require.Equal(t, "topaz-secret", receivedAPIKey)
	require.Equal(t, []string{"rhea-1", "slf-2"}, models)
}

func TestFetchUpscaleModelsRejectsUnavailableSystem(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"isAvailable":false,"availabilityMessage":"maintenance","supportedModels":["prob-4"]}`)
	}))
	defer server.Close()

	_, err := FetchUpscaleModels(server.URL, "topaz-secret", "")

	require.ErrorContains(t, err, "maintenance")
	require.NotContains(t, err.Error(), "topaz-secret")
}

func TestValidateRequestRequiresOneSupportedVideo(t *testing.T) {
	tests := []struct {
		name       string
		fields     map[string]string
		filename   string
		fileBytes  []byte
		wantErrSub string
	}{
		{
			name:       "missing file",
			fields:     validTopazFields(),
			wantErrSub: "input_reference",
		},
		{
			name:       "unsupported file",
			fields:     validTopazFields(),
			filename:   "source.avi",
			fileBytes:  []byte("video"),
			wantErrSub: "MP4, MOV, or MKV",
		},
		{
			name: "missing size",
			fields: map[string]string{
				"model":             "prob-4",
				"output_frame_rate": "30",
			},
			filename:   "source.mp4",
			fileBytes:  []byte("video"),
			wantErrSub: "size",
		},
		{
			name: "invalid frame rate",
			fields: map[string]string{
				"model":             "prob-4",
				"size":              "1920x1080",
				"output_frame_rate": "0",
			},
			filename:   "source.mp4",
			fileBytes:  []byte("video"),
			wantErrSub: "output_frame_rate",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx := newTopazMultipartContext(t, tt.fields, tt.filename, tt.fileBytes)
			info := &relaycommon.RelayInfo{}
			adaptor := &TaskAdaptor{}

			taskErr := adaptor.ValidateRequestAndSetAction(ctx, info)

			require.NotNil(t, taskErr)
			require.Contains(t, taskErr.Message, tt.wantErrSub)
		})
	}
}

func TestBuildRequestBodyCreatesExpressPayload(t *testing.T) {
	ctx := newTopazMultipartContext(t, validTopazFields(), "source.mp4", []byte("video"))
	info := &relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{UpstreamModelName: "prob-4"}}
	adaptor := &TaskAdaptor{}
	require.Nil(t, adaptor.ValidateRequestAndSetAction(ctx, info))

	body, err := adaptor.BuildRequestBody(ctx, info)
	require.NoError(t, err)
	payloadBytes, err := io.ReadAll(body)
	require.NoError(t, err)

	var payload expressRequest
	require.NoError(t, common.Unmarshal(payloadBytes, &payload))
	require.Equal(t, "mp4", payload.Source.Container)
	require.Len(t, payload.Filters, 1)
	require.Equal(t, "prob-4", payload.Filters[0].Model)
	require.Equal(t, 1920, payload.Output.Resolution.Width)
	require.Equal(t, 1080, payload.Output.Resolution.Height)
	require.Equal(t, 30.0, payload.Output.FrameRate)
	require.Equal(t, "AAC", payload.Output.AudioCodec)
	require.Equal(t, "Copy", payload.Output.AudioTransfer)
	require.Equal(t, "H264", payload.Output.VideoEncoder)
}

func TestBuildRequestHeaderUsesXAPIKey(t *testing.T) {
	adaptor := &TaskAdaptor{}
	adaptor.Init(&relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{ApiKey: "topaz-secret"}})
	req := httptest.NewRequest(http.MethodPost, "https://api.topazlabs.com/video/express", nil)

	err := adaptor.BuildRequestHeader(nil, req, nil)

	require.NoError(t, err)
	require.Equal(t, "topaz-secret", req.Header.Get("X-API-Key"))
	require.Empty(t, req.Header.Get("Authorization"))
}

func TestDoResponseUploadsWithoutLeakingTopazKey(t *testing.T) {
	var uploadBody []byte
	var uploadAPIKey string
	uploadServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPut, r.Method)
		uploadAPIKey = r.Header.Get("X-API-Key")
		body, err := io.ReadAll(r.Body)
		require.NoError(t, err)
		uploadBody = body
		w.WriteHeader(http.StatusOK)
	}))
	defer uploadServer.Close()

	ctx := newTopazMultipartContext(t, validTopazFields(), "source.mp4", []byte("source-video"))
	recorder := ctx.MustGet("test_recorder").(*httptest.ResponseRecorder)
	info := &relaycommon.RelayInfo{
		OriginModelName: "prob-4",
		ChannelMeta: &relaycommon.ChannelMeta{
			ApiKey:            "topaz-secret",
			UpstreamModelName: "prob-4",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_public"},
	}
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	adaptor.httpClient = uploadServer.Client()
	require.Nil(t, adaptor.ValidateRequestAndSetAction(ctx, info))
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body: io.NopCloser(strings.NewReader(
			`{"requestId":"11111111-1111-1111-1111-111111111111","uploadUrls":["` + uploadServer.URL + `"]}`,
		)),
	}

	taskID, taskData, taskErr := adaptor.DoResponse(ctx, response, info)

	require.Nil(t, taskErr)
	require.Equal(t, "11111111-1111-1111-1111-111111111111", taskID)
	require.Equal(t, []byte("source-video"), uploadBody)
	require.Empty(t, uploadAPIKey)
	require.NotContains(t, string(taskData), uploadServer.URL)
	require.NotContains(t, recorder.Body.String(), taskID)
	require.Contains(t, recorder.Body.String(), "task_public")
}

func TestUploadSourceDoesNotFollowRedirects(t *testing.T) {
	redirectTargetCalled := false
	redirectTarget := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		redirectTargetCalled = true
		w.WriteHeader(http.StatusOK)
	}))
	defer redirectTarget.Close()

	redirectServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Location", redirectTarget.URL)
		w.WriteHeader(http.StatusTemporaryRedirect)
	}))
	defer redirectServer.Close()

	ctx := newTopazMultipartContext(t, validTopazFields(), "source.mp4", []byte("source-video"))
	info := &relaycommon.RelayInfo{TaskRelayInfo: &relaycommon.TaskRelayInfo{}}
	adaptor := &TaskAdaptor{httpClient: redirectServer.Client()}
	require.Nil(t, adaptor.ValidateRequestAndSetAction(ctx, info))
	request, err := getVideoRequest(ctx)
	require.NoError(t, err)

	err = adaptor.uploadSource(redirectServer.URL, request)

	require.ErrorContains(t, err, "HTTP 307")
	require.False(t, redirectTargetCalled)
}

func TestUploadSourceRedactsPresignedURLFromNetworkErrors(t *testing.T) {
	const uploadURL = "https://upload.example/video?X-Amz-Signature=do-not-leak"
	ctx := newTopazMultipartContext(t, validTopazFields(), "source.mp4", []byte("source-video"))
	info := &relaycommon.RelayInfo{TaskRelayInfo: &relaycommon.TaskRelayInfo{}}
	adaptor := &TaskAdaptor{httpClient: &http.Client{Transport: roundTripperFunc(func(req *http.Request) (*http.Response, error) {
		return nil, fmt.Errorf("dial failed for %s", req.URL.String())
	})}}
	require.Nil(t, adaptor.ValidateRequestAndSetAction(ctx, info))
	request, err := getVideoRequest(ctx)
	require.NoError(t, err)

	err = adaptor.uploadSource(uploadURL, request)

	require.Error(t, err)
	require.NotContains(t, err.Error(), "do-not-leak")
	require.NotContains(t, err.Error(), uploadURL)
}

func TestParseTaskResultMapsTopazStates(t *testing.T) {
	tests := []struct {
		status       string
		wantStatus   string
		wantURL      string
		wantProgress string
	}{
		{status: "requested", wantStatus: "SUBMITTED", wantProgress: "0%"},
		{status: "accepted", wantStatus: "SUBMITTED", wantProgress: "0%"},
		{status: "preprocessing", wantStatus: "IN_PROGRESS", wantProgress: "12%"},
		{status: "processing", wantStatus: "IN_PROGRESS", wantProgress: "57.5%"},
		{status: "postprocessing", wantStatus: "IN_PROGRESS", wantProgress: "99%"},
		{status: "complete", wantStatus: "SUCCESS", wantURL: "https://download.example/result.mp4", wantProgress: "100%"},
		{status: "canceled", wantStatus: "FAILURE", wantProgress: "100%"},
		{status: "failed", wantStatus: "FAILURE", wantProgress: "100%"},
	}

	for _, tt := range tests {
		t.Run(tt.status, func(t *testing.T) {
			progress := strings.TrimSuffix(tt.wantProgress, "%")
			body := []byte(`{"status":"` + tt.status + `","progress":` + progress + `,"message":"upstream message","download":{"url":"` + tt.wantURL + `"}}`)
			result, err := (&TaskAdaptor{}).ParseTaskResult(body)

			require.NoError(t, err)
			require.Equal(t, tt.wantStatus, result.Status)
			require.Equal(t, tt.wantProgress, result.Progress)
			require.Equal(t, tt.wantURL, result.Url)
		})
	}
}

func validTopazFields() map[string]string {
	return map[string]string{
		"model":             "prob-4",
		"size":              "1920x1080",
		"output_frame_rate": "30",
	}
}

func newTopazMultipartContext(t *testing.T, fields map[string]string, filename string, fileBytes []byte) *gin.Context {
	t.Helper()

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for key, value := range fields {
		require.NoError(t, writer.WriteField(key, value))
	}
	if filename != "" {
		part, err := writer.CreateFormFile("input_reference", filename)
		require.NoError(t, err)
		_, err = part.Write(fileBytes)
		require.NoError(t, err)
	}
	require.NoError(t, writer.Close())

	req := httptest.NewRequest(http.MethodPost, "/v1/videos", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = req
	ctx.Set("test_recorder", recorder)
	return ctx
}
