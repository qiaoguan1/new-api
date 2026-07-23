package topaz

import (
	"bytes"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const (
	topazRequestContextKey = "topaz_video_request"
	maxSourceVideoBytes    = int64(500 * 1024 * 1024)
	maxTopazMultipartBytes = maxSourceVideoBytes + 1024*1024
	maxTopazJSONBytes      = int64(1024 * 1024)
	topazRequestTimeout    = 30 * time.Second
	topazUploadTimeout     = 30 * time.Minute
)

type resolution struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

type expressSource struct {
	Container string `json:"container"`
}

type upscaleFilter struct {
	Model string `json:"model"`
}

type expressOutput struct {
	Resolution              resolution `json:"resolution"`
	FrameRate               float64    `json:"frameRate"`
	AudioCodec              string     `json:"audioCodec"`
	AudioTransfer           string     `json:"audioTransfer"`
	VideoEncoder            string     `json:"videoEncoder"`
	DynamicCompressionLevel string     `json:"dynamicCompressionLevel"`
	Container               string     `json:"container"`
}

type expressRequest struct {
	Source  expressSource   `json:"source"`
	Filters []upscaleFilter `json:"filters"`
	Output  expressOutput   `json:"output"`
}

type expressResponse struct {
	RequestID  string   `json:"requestId"`
	UploadURLs []string `json:"uploadUrls"`
}

type statusResponse struct {
	Status   string  `json:"status"`
	Progress float64 `json:"progress"`
	Message  string  `json:"message"`
	Download struct {
		URL       string `json:"url"`
		ExpiresIn int64  `json:"expiresIn"`
		ExpiresAt int64  `json:"expiresAt"`
	} `json:"download"`
}

type videoRequest struct {
	Model           string
	Size            string
	FrameRate       float64
	SourceContainer string
	OutputContainer string
	VideoEncoder    string
	File            *multipart.FileHeader
}

// TaskAdaptor converts the OpenAI-compatible asynchronous video API into Topaz Video API calls.
type TaskAdaptor struct {
	taskcommon.BaseBilling
	apiKey     string
	baseURL    string
	proxy      string
	httpClient *http.Client
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.apiKey = info.ApiKey
	a.baseURL = strings.TrimRight(info.ChannelBaseUrl, "/")
	if info.ChannelMeta != nil {
		a.proxy = info.ChannelSetting.Proxy
	}
}

func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) *dto.TaskError {
	if !strings.Contains(strings.ToLower(c.GetHeader("Content-Type")), "multipart/form-data") {
		return topazTaskError("Topaz video upscaling requires multipart/form-data", "invalid_request")
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxTopazMultipartBytes)
	form, err := c.MultipartForm()
	if err != nil {
		return topazTaskError("invalid multipart form", "invalid_multipart_form")
	}

	modelName := strings.TrimSpace(firstFormValue(form, "model"))
	if modelName == "" {
		return topazTaskError("model field is required", "missing_model")
	}
	files := form.File["input_reference"]
	if len(files) != 1 {
		return topazTaskError("exactly one input_reference video file is required", "invalid_input_reference")
	}
	fileHeader := files[0]
	if fileHeader.Size < 0 || fileHeader.Size > maxSourceVideoBytes {
		return topazTaskError("input_reference exceeds the 500 MB Topaz limit", "file_too_large")
	}
	sourceContainer, ok := containerFromFilename(fileHeader.Filename)
	if !ok {
		return topazTaskError("input_reference must be an MP4, MOV, or MKV video", "unsupported_video_container")
	}

	size := strings.TrimSpace(firstFormValue(form, "size"))
	width, height, err := parseResolution(size)
	if err != nil {
		return topazTaskError(err.Error(), "invalid_size")
	}
	frameRate, err := strconv.ParseFloat(strings.TrimSpace(firstFormValue(form, "output_frame_rate")), 64)
	if err != nil || frameRate <= 0 || frameRate > 240 {
		return topazTaskError("output_frame_rate must be greater than 0 and at most 240", "invalid_frame_rate")
	}

	outputContainer := strings.ToLower(strings.TrimSpace(firstFormValue(form, "output_container")))
	if outputContainer == "" {
		outputContainer = "mp4"
	}
	if !isContainerSupported(outputContainer) {
		return topazTaskError("output_container must be mp4, mov, or mkv", "invalid_output_container")
	}

	videoEncoder := strings.ToUpper(strings.TrimSpace(firstFormValue(form, "video_encoder")))
	if videoEncoder == "" {
		if width <= 4096 && height <= 4096 {
			videoEncoder = "H264"
		} else {
			videoEncoder = "H265"
		}
	}
	if err := validateEncoderResolution(videoEncoder, width, height); err != nil {
		return topazTaskError(err.Error(), "invalid_video_encoder")
	}

	request := videoRequest{
		Model:           modelName,
		Size:            size,
		FrameRate:       frameRate,
		SourceContainer: sourceContainer,
		OutputContainer: outputContainer,
		VideoEncoder:    videoEncoder,
		File:            fileHeader,
	}
	c.Set(topazRequestContextKey, request)
	c.Set("task_request", relaycommon.TaskSubmitReq{Model: modelName, Size: size})
	if info.TaskRelayInfo == nil {
		info.TaskRelayInfo = &relaycommon.TaskRelayInfo{}
	}
	info.Action = constant.TaskActionGenerate
	return nil
}

func (a *TaskAdaptor) BuildRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	if strings.TrimSpace(a.baseURL) == "" {
		return "", fmt.Errorf("Topaz base URL is required")
	}
	return a.baseURL + "/video/express", nil
}

func (a *TaskAdaptor) BuildRequestHeader(_ *gin.Context, req *http.Request, _ *relaycommon.RelayInfo) error {
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", a.apiKey)
	return nil
}

func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	request, err := getVideoRequest(c)
	if err != nil {
		return nil, err
	}
	if !isUpscaleModel(info.UpstreamModelName) {
		return nil, fmt.Errorf("Topaz model %s is not a video upscaling model", info.UpstreamModelName)
	}
	width, height, err := parseResolution(request.Size)
	if err != nil {
		return nil, err
	}
	payload := expressRequest{
		Source: expressSource{Container: request.SourceContainer},
		Filters: []upscaleFilter{{
			Model: info.UpstreamModelName,
		}},
		Output: expressOutput{
			Resolution:              resolution{Width: width, Height: height},
			FrameRate:               request.FrameRate,
			AudioCodec:              "AAC",
			AudioTransfer:           "Copy",
			VideoEncoder:            request.VideoEncoder,
			DynamicCompressionLevel: "High",
			Container:               request.OutputContainer,
		},
	}
	body, err := common.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal Topaz express request: %w", err)
	}
	return bytes.NewReader(body), nil
}

func (a *TaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	return channel.DoTaskApiRequest(a, c, info, requestBody)
}

func (a *TaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (string, []byte, *dto.TaskError) {
	defer resp.Body.Close()
	var created expressResponse
	if err := common.DecodeJson(io.LimitReader(resp.Body, maxTopazJSONBytes), &created); err != nil {
		return "", nil, topazUpstreamTaskError("invalid Topaz create response", "invalid_response")
	}
	created.RequestID = strings.TrimSpace(created.RequestID)
	if created.RequestID == "" || len(created.UploadURLs) != 1 {
		return "", nil, topazUpstreamTaskError("Topaz did not return one upload URL", "invalid_response")
	}
	request, err := getVideoRequest(c)
	if err != nil {
		return "", nil, topazTaskError(err.Error(), "invalid_request")
	}
	if err := a.uploadSource(created.UploadURLs[0], request); err != nil {
		return "", nil, topazUpstreamTaskError("failed to upload input_reference to Topaz", "upload_failed")
	}

	publicVideo := dto.NewOpenAIVideo()
	publicVideo.ID = info.PublicTaskID
	publicVideo.TaskID = info.PublicTaskID
	publicVideo.Model = info.OriginModelName
	publicVideo.Status = dto.VideoStatusQueued
	publicVideo.Progress = 0
	publicVideo.CreatedAt = time.Now().Unix()
	publicVideo.Size = request.Size
	taskData, err := common.Marshal(publicVideo)
	if err != nil {
		return "", nil, topazTaskError("encode public task response", "invalid_response")
	}
	c.JSON(http.StatusOK, publicVideo)
	return created.RequestID, taskData, nil
}

func (a *TaskAdaptor) uploadSource(uploadURL string, request videoRequest) error {
	parsedURL, err := url.Parse(strings.TrimSpace(uploadURL))
	if err != nil || parsedURL.Scheme != "https" || parsedURL.Host == "" {
		return fmt.Errorf("Topaz returned an invalid HTTPS upload URL")
	}
	file, err := request.File.Open()
	if err != nil {
		return fmt.Errorf("open input_reference: %w", err)
	}
	defer file.Close()
	uploadReq, err := http.NewRequest(http.MethodPut, parsedURL.String(), file)
	if err != nil {
		return fmt.Errorf("create Topaz upload request: %w", err)
	}
	uploadReq.ContentLength = request.File.Size
	uploadReq.Header.Set("Content-Type", contentTypeForContainer(request.SourceContainer))

	client := a.httpClient
	if client == nil {
		client, err = service.GetHttpClientWithProxy(a.proxy)
		if err != nil {
			return fmt.Errorf("create upload client: %w", err)
		}
	}
	client = topazHTTPClient(client, topazUploadTimeout)
	uploadResp, err := client.Do(uploadReq)
	if err != nil {
		// net/http errors include the request URL. Never propagate a presigned URL or its signature.
		return fmt.Errorf("Topaz upload request failed")
	}
	defer uploadResp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(uploadResp.Body, 4096))
	if uploadResp.StatusCode < http.StatusOK || uploadResp.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("Topaz upload returned HTTP %d", uploadResp.StatusCode)
	}
	return nil
}

func (a *TaskAdaptor) FetchTask(baseURL, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok || strings.TrimSpace(taskID) == "" {
		return nil, fmt.Errorf("invalid task_id")
	}
	uri := strings.TrimRight(baseURL, "/") + "/video/" + url.PathEscape(taskID) + "/status"
	req, err := http.NewRequest(http.MethodGet, uri, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-API-Key", key)
	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("create Topaz status client: %w", err)
	}
	client = topazHTTPClient(client, topazRequestTimeout)
	return client.Do(req)
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	var status statusResponse
	if err := common.Unmarshal(respBody, &status); err != nil {
		return nil, fmt.Errorf("decode Topaz task status: %w", err)
	}
	result := &relaycommon.TaskInfo{Code: 0, Progress: formatProgress(status.Progress)}
	switch status.Status {
	case "requested", "accepted", "initializing":
		result.Status = model.TaskStatusSubmitted
	case "preprocessing", "processing", "postprocessing", "canceling":
		result.Status = model.TaskStatusInProgress
	case "complete":
		result.Status = model.TaskStatusSuccess
		result.Progress = "100%"
		result.Url = strings.TrimSpace(status.Download.URL)
		if result.Url == "" {
			return nil, fmt.Errorf("Topaz completed without a download URL")
		}
	case "canceled", "failed":
		result.Status = model.TaskStatusFailure
		result.Progress = "100%"
		result.Reason = strings.TrimSpace(status.Message)
		if result.Reason == "" {
			result.Reason = "Topaz video processing failed"
		}
	default:
		return nil, fmt.Errorf("unknown Topaz task status %q", status.Status)
	}
	return result, nil
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(task *model.Task) ([]byte, error) {
	video := dto.NewOpenAIVideo()
	video.ID = task.TaskID
	video.TaskID = task.TaskID
	video.Model = task.Properties.OriginModelName
	video.Status = task.Status.ToVideoStatus()
	video.SetProgressStr(task.Progress)
	video.CreatedAt = task.CreatedAt
	if task.Status == model.TaskStatusSuccess || task.Status == model.TaskStatusFailure {
		video.CompletedAt = task.UpdatedAt
	}
	if resultURL := strings.TrimSpace(task.GetResultURL()); resultURL != "" && task.Status == model.TaskStatusSuccess {
		video.SetMetadata("url", resultURL)
	}
	if task.Status == model.TaskStatusFailure {
		video.Error = &dto.OpenAIVideoError{Message: task.FailReason, Code: "video_processing_failed"}
	}
	return common.Marshal(video)
}

func (a *TaskAdaptor) GetModelList() []string {
	return append([]string(nil), ModelList...)
}

func (a *TaskAdaptor) GetChannelName() string {
	return ChannelName
}

func topazTaskError(message, code string) *dto.TaskError {
	err := fmt.Errorf("%s", message)
	return &dto.TaskError{Code: code, Message: message, StatusCode: http.StatusBadRequest, LocalError: true, Error: err}
}

func topazUpstreamTaskError(message, code string) *dto.TaskError {
	err := fmt.Errorf("%s", message)
	return &dto.TaskError{Code: code, Message: message, StatusCode: http.StatusBadGateway, Error: err}
}

func getVideoRequest(c *gin.Context) (videoRequest, error) {
	value, ok := c.Get(topazRequestContextKey)
	if !ok {
		return videoRequest{}, fmt.Errorf("Topaz video request is missing")
	}
	request, ok := value.(videoRequest)
	if !ok {
		return videoRequest{}, fmt.Errorf("Topaz video request is invalid")
	}
	return request, nil
}

func firstFormValue(form *multipart.Form, key string) string {
	values := form.Value[key]
	if len(values) == 0 {
		return ""
	}
	return values[0]
}

func containerFromFilename(filename string) (string, bool) {
	container := strings.TrimPrefix(strings.ToLower(filepath.Ext(filename)), ".")
	return container, isContainerSupported(container)
}

func isContainerSupported(container string) bool {
	switch container {
	case "mp4", "mov", "mkv":
		return true
	default:
		return false
	}
}

func parseResolution(value string) (int, int, error) {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(value)), "x")
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("size must use WIDTHxHEIGHT format")
	}
	width, widthErr := strconv.Atoi(parts[0])
	height, heightErr := strconv.Atoi(parts[1])
	if widthErr != nil || heightErr != nil || width <= 0 || height <= 0 {
		return 0, 0, fmt.Errorf("size must contain positive width and height")
	}
	return width, height, nil
}

func validateEncoderResolution(encoder string, width, height int) error {
	maxWidth, maxHeight := 0, 0
	switch encoder {
	case "H264":
		maxWidth, maxHeight = 4096, 4096
	case "H265":
		maxWidth, maxHeight = 8192, 8192
	case "AV1":
		maxWidth, maxHeight = 16384, 8704
	case "VP9":
		maxWidth, maxHeight = 8192, 8192
	default:
		return fmt.Errorf("video_encoder must be H264, H265, AV1, or VP9")
	}
	if width > maxWidth || height > maxHeight {
		return fmt.Errorf("size exceeds %s maximum of %dx%d", encoder, maxWidth, maxHeight)
	}
	return nil
}

func contentTypeForContainer(container string) string {
	switch container {
	case "mov":
		return "video/quicktime"
	case "mkv":
		return "video/x-matroska"
	default:
		return "video/mp4"
	}
}

func formatProgress(progress float64) string {
	if progress < 0 {
		progress = 0
	}
	if progress > 100 {
		progress = 100
	}
	return strconv.FormatFloat(progress, 'f', -1, 64) + "%"
}

func topazHTTPClient(client *http.Client, timeout time.Duration) *http.Client {
	if client == nil {
		client = http.DefaultClient
	}
	cloned := *client
	cloned.Timeout = timeout
	cloned.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return &cloned
}
