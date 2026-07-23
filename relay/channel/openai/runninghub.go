package openai

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

const (
	runningHubPollInterval = 2 * time.Second
	runningHubPollMaxRetry = 90
	runningHubDefaultQuery = "https://www.runninghub.cn/openapi/v2/query"
)

func isRunningHubEndpoint(baseURL string) bool {
	lower := strings.ToLower(strings.TrimSpace(baseURL))
	return strings.Contains(lower, "runninghub.cn")
}

func isRunningHubWorkflowEndpoint(baseURL string) bool {
	lower := strings.ToLower(strings.TrimSpace(baseURL))
	return strings.Contains(lower, "/openapi/v2/run/workflow/") || strings.Contains(lower, "/run/workflow/")
}

func runningHubSubmitURL(baseURL string) string {
	return strings.TrimRight(strings.TrimSpace(baseURL), "/")
}

func runningHubQueryURL(baseURL string) string {
	trimmed := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if trimmed == "" {
		return runningHubDefaultQuery
	}
	parsed, err := url.Parse(trimmed)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return runningHubDefaultQuery
	}
	parsed.Path = "/openapi/v2/query"
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return parsed.String()
}

func runningHubAspectRatio(size string) string {
	normalized := strings.ToLower(strings.TrimSpace(size))
	switch normalized {
	case "1024x1024", "1:1":
		return "1:1"
	case "1792x1024", "16:9":
		return "16:9"
	case "1024x1792", "9:16":
		return "9:16"
	case "1536x1024", "3:2":
		return "3:2"
	case "1024x1536", "2:3":
		return "2:3"
	default:
		if strings.Contains(normalized, "x") {
			parts := strings.Split(normalized, "x")
			if len(parts) == 2 {
				left := strings.TrimSpace(parts[0])
				right := strings.TrimSpace(parts[1])
				if left != "" && right != "" {
					return fmt.Sprintf("%s:%s", left, right)
				}
			}
		}
		return "1:1"
	}
}

func runningHubResolution(size string) string {
	normalized := strings.ToLower(strings.TrimSpace(size))
	switch normalized {
	case "1792x1024", "1024x1792", "1536x1024", "1024x1536":
		return "2k"
	case "2048x2048", "2048x1536", "1536x2048", "4096x4096", "4096x2048", "2048x4096":
		return "4k"
	default:
		return "1k"
	}
}

func runningHubCollectImageURLs(request dto.ImageRequest) []string {
	seen := make(map[string]struct{})
	urls := make([]string, 0, 4)
	add := func(value string) {
		trimmed := strings.TrimSpace(value)
		if trimmed == "" {
			return
		}
		if _, ok := seen[trimmed]; ok {
			return
		}
		seen[trimmed] = struct{}{}
		urls = append(urls, trimmed)
	}
	readRaw := func(raw []byte) {
		if len(raw) == 0 {
			return
		}
		var single string
		if err := common.Unmarshal(raw, &single); err == nil {
			add(single)
			return
		}
		var stringsArr []string
		if err := common.Unmarshal(raw, &stringsArr); err == nil {
			for _, item := range stringsArr {
				add(item)
			}
			return
		}
		var anyArr []any
		if err := common.Unmarshal(raw, &anyArr); err == nil {
			for _, item := range anyArr {
				if str, ok := item.(string); ok {
					add(str)
				}
			}
		}
	}

	readRaw(request.Image)
	readRaw(request.Images)
	if request.Extra != nil {
		for _, key := range []string{"imageUrls", "image_urls", "images", "input_reference", "input_references"} {
			if raw, ok := request.Extra[key]; ok {
				readRaw(raw)
			}
		}
	}
	return urls
}

func runningHubWorkflowNodeInfoList(request dto.ImageRequest) []map[string]any {
	urls := runningHubCollectImageURLs(request)
	nodes := make([]map[string]any, 0, len(urls)+4)
	for _, imageURL := range urls {
		nodes = append(nodes, map[string]any{
			"fieldName":  "image",
			"fieldValue": imageURL,
		})
	}
	if request.Extra != nil {
		for _, key := range []string{"nodeInfoList", "node_info_list"} {
			if raw, ok := request.Extra[key]; ok && len(raw) > 0 {
				var explicit []map[string]any
				if err := common.Unmarshal(raw, &explicit); err == nil {
					return explicit
				}
				var anyList []any
				if err := common.Unmarshal(raw, &anyList); err == nil {
					converted := make([]map[string]any, 0, len(anyList))
					for _, item := range anyList {
						if node, ok := item.(map[string]any); ok {
							converted = append(converted, node)
						}
					}
					return converted
				}
			}
		}
	}
	return nodes
}

func runningHubWorkflowSubmitPayload(request dto.ImageRequest) (map[string]any, error) {
	payload := map[string]any{
		"addMetadata":      true,
		"nodeInfoList":     runningHubWorkflowNodeInfoList(request),
		"instanceType":     "default",
		"usePersonalQueue": false,
	}
	if request.Extra != nil {
		for _, key := range []string{"addMetadata", "instanceType", "usePersonalQueue", "retainSeconds", "webhookUrl"} {
			if raw, ok := request.Extra[key]; ok && len(raw) > 0 {
				var value any
				if err := common.Unmarshal(raw, &value); err == nil {
					payload[key] = value
				}
			}
		}
	}
	return payload, nil
}

func runningHubImageSubmitPayload(request dto.ImageRequest, baseURL string) (map[string]any, error) {
	if isRunningHubWorkflowEndpoint(baseURL) {
		return runningHubWorkflowSubmitPayload(request)
	}
	aspectRatio := runningHubAspectRatio(request.Size)
	if aspectRatio == "1:1" && strings.TrimSpace(request.Size) == "" {
		aspectRatio = "auto"
	}
	payload := map[string]any{
		"prompt":      strings.TrimSpace(request.Prompt),
		"aspectRatio": aspectRatio,
		"resolution":  runningHubResolution(request.Size),
	}
	if urls := runningHubCollectImageURLs(request); len(urls) > 0 {
		payload["imageUrls"] = urls
	}
	return payload, nil
}

func runningHubFirstString(payload map[string]any, keys []string) string {
	for _, key := range keys {
		if value, ok := payload[key].(string); ok {
			if trimmed := strings.TrimSpace(value); trimmed != "" {
				return trimmed
			}
		}
	}
	return ""
}

func runningHubTaskIDFromAny(value any) string {
	switch data := value.(type) {
	case map[string]any:
		for _, key := range []string{"taskId", "task_id", "id"} {
			if s, ok := data[key].(string); ok {
				if trimmed := strings.TrimSpace(s); trimmed != "" {
					return trimmed
				}
			}
		}
		for _, key := range []string{"data", "result", "output"} {
			if child, ok := data[key]; ok {
				if taskID := runningHubTaskIDFromAny(child); taskID != "" {
					return taskID
				}
			}
		}
	case []any:
		for _, item := range data {
			if taskID := runningHubTaskIDFromAny(item); taskID != "" {
				return taskID
			}
		}
	case string:
		if trimmed := strings.TrimSpace(data); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func runningHubTaskID(payload map[string]any) string {
	if payload == nil {
		return ""
	}
	for _, key := range []string{"taskId", "task_id", "id"} {
		if value, ok := payload[key].(string); ok {
			if trimmed := strings.TrimSpace(value); trimmed != "" {
				return trimmed
			}
		}
	}
	for _, key := range []string{"data", "result", "output"} {
		if child, ok := payload[key]; ok {
			if taskID := runningHubTaskIDFromAny(child); taskID != "" {
				return taskID
			}
		}
	}
	return ""
}

func runningHubStatusFromAny(value any) string {
	switch data := value.(type) {
	case map[string]any:
		for _, key := range []string{"status", "task_status", "state"} {
			if s, ok := data[key].(string); ok {
				if trimmed := strings.TrimSpace(strings.ToLower(s)); trimmed != "" {
					return trimmed
				}
			}
		}
		for _, key := range []string{"data", "result", "output"} {
			if child, ok := data[key]; ok {
				if status := runningHubStatusFromAny(child); status != "" {
					return status
				}
			}
		}
	case []any:
		for _, item := range data {
			if status := runningHubStatusFromAny(item); status != "" {
				return status
			}
		}
	case string:
		return strings.TrimSpace(strings.ToLower(data))
	}
	return ""
}

func runningHubStatus(payload map[string]any) string {
	if payload == nil {
		return ""
	}
	for _, key := range []string{"status", "task_status", "state"} {
		if s, ok := payload[key].(string); ok {
			if trimmed := strings.TrimSpace(strings.ToLower(s)); trimmed != "" {
				return trimmed
			}
		}
	}
	for _, key := range []string{"data", "result", "output"} {
		if child, ok := payload[key]; ok {
			if status := runningHubStatusFromAny(child); status != "" {
				return status
			}
		}
	}
	return ""
}

func runningHubMessageFromAny(value any) string {
	switch data := value.(type) {
	case map[string]any:
		for _, key := range []string{"message", "error", "reason", "errorMessage", "error_message"} {
			if s, ok := data[key].(string); ok {
				if trimmed := strings.TrimSpace(s); trimmed != "" {
					return trimmed
				}
			}
		}
		for _, key := range []string{"data", "result", "output"} {
			if child, ok := data[key]; ok {
				if msg := runningHubMessageFromAny(child); msg != "" {
					return msg
				}
			}
		}
	case []any:
		for _, item := range data {
			if msg := runningHubMessageFromAny(item); msg != "" {
				return msg
			}
		}
	case string:
		return strings.TrimSpace(data)
	}
	return ""
}

func runningHubMessage(payload map[string]any) string {
	if payload == nil {
		return ""
	}
	for _, key := range []string{"message", "error", "reason", "errorMessage", "error_message"} {
		if s, ok := payload[key].(string); ok {
			if trimmed := strings.TrimSpace(s); trimmed != "" {
				return trimmed
			}
		}
	}
	for _, key := range []string{"data", "result", "output"} {
		if child, ok := payload[key]; ok {
			if msg := runningHubMessageFromAny(child); msg != "" {
				return msg
			}
		}
	}
	return ""
}

func runningHubImageDataFromAny(value any) []dto.ImageData {
	result := make([]dto.ImageData, 0, 2)
	seen := make(map[string]struct{})
	add := func(data dto.ImageData) {
		key := data.Url + "|" + data.B64Json
		if key == "|" {
			return
		}
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		result = append(result, data)
	}

	var walk func(any)
	walk = func(v any) {
		switch data := v.(type) {
		case map[string]any:
			if s := runningHubFirstString(data, []string{"url", "fileUrl", "file_url", "outputUrl", "output_url", "downloadUrl", "download_url", "videoUrl", "video_url"}); s != "" {
				add(dto.ImageData{Url: s})
				return
			}
			if s := runningHubFirstString(data, []string{"b64_json", "base64", "image_base64"}); s != "" {
				add(dto.ImageData{B64Json: s})
				return
			}
			for _, key := range []string{"data", "result", "output", "images", "items", "choices", "candidates", "files"} {
				if child, ok := data[key]; ok {
					walk(child)
				}
			}
		case []any:
			for _, item := range data {
				walk(item)
			}
		case string:
			trimmed := strings.TrimSpace(data)
			if trimmed == "" {
				return
			}
			if strings.HasPrefix(trimmed, "http://") || strings.HasPrefix(trimmed, "https://") {
				add(dto.ImageData{Url: trimmed})
				return
			}
			if strings.HasPrefix(trimmed, "data:image/") {
				add(dto.ImageData{B64Json: trimmed})
				return
			}
			if len(trimmed) > 64 {
				add(dto.ImageData{B64Json: trimmed})
			}
		}
	}
	walk(value)
	return result
}

func runningHubImageData(payload map[string]any) []dto.ImageData {
	if payload == nil {
		return nil
	}
	return runningHubImageDataFromAny(payload)
}

func runningHubImageRequestFormat(req *dto.ImageRequest) string {
	if req == nil {
		return "url"
	}
	if strings.EqualFold(strings.TrimSpace(req.ResponseFormat), "b64_json") || strings.EqualFold(strings.TrimSpace(req.ResponseFormat), "base64") {
		return "b64_json"
	}
	return "url"
}

func downloadRunningHubImages(imageData []dto.ImageData) ([]dto.ImageData, error) {
	converted := make([]dto.ImageData, 0, len(imageData))
	for _, item := range imageData {
		if item.B64Json != "" {
			converted = append(converted, item)
			continue
		}
		if item.Url == "" {
			continue
		}
		_, data, err := service.GetImageFromUrl(item.Url)
		if err != nil {
			return nil, err
		}
		converted = append(converted, dto.ImageData{B64Json: data})
	}
	return converted, nil
}

func runningHubImageSubmitHandler(c *gin.Context, info *relaycommon.RelayInfo, resp *http.Response) (*dto.Usage, *types.NewAPIError) {
	if resp == nil || resp.Body == nil {
		return nil, types.NewOpenAIError(fmt.Errorf("invalid response"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
	}
	defer service.CloseResponseBodyGracefully(resp)

	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}

	if resp.StatusCode >= 400 {
		message := strings.TrimSpace(string(responseBody))
		if message == "" {
			message = resp.Status
		}
		return nil, types.NewOpenAIError(fmt.Errorf("%s", message), types.ErrorCodeBadResponseBody, resp.StatusCode)
	}

	var payload map[string]any
	if err := common.Unmarshal(responseBody, &payload); err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}

	if status := runningHubStatus(payload); status != "" && (strings.Contains(status, "fail") || strings.Contains(status, "error") || strings.Contains(status, "cancel") || strings.Contains(status, "timeout")) {
		message := runningHubMessage(payload)
		if message == "" {
			message = status
		}
		return nil, types.NewOpenAIError(fmt.Errorf("%s", message), types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}

	imageData := runningHubImageData(payload)
	taskID := runningHubTaskID(payload)
	if len(imageData) == 0 && taskID != "" {
		p, pollErr := pollRunningHubImageData(info, taskID)
		if pollErr != nil {
			return nil, types.NewOpenAIError(pollErr, types.ErrorCodeBadResponseBody, http.StatusBadGateway)
		}
		imageData = p
	}

	if len(imageData) == 0 {
		if message := runningHubMessage(payload); message != "" {
			return nil, types.NewOpenAIError(fmt.Errorf("%s", message), types.ErrorCodeBadResponseBody, http.StatusBadGateway)
		}
		return nil, types.NewOpenAIError(fmt.Errorf("runninghub returned no image data"), types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}

	imageReq, _ := info.Request.(*dto.ImageRequest)
	if runningHubImageRequestFormat(imageReq) == "b64_json" {
		converted, convErr := downloadRunningHubImages(imageData)
		if convErr != nil {
			return nil, types.NewOpenAIError(convErr, types.ErrorCodeBadResponseBody, http.StatusBadGateway)
		}
		if len(converted) > 0 {
			imageData = converted
		}
	}

	imageResponse := dto.ImageResponse{
		Created: time.Now().Unix(),
		Data:    make([]dto.ImageData, 0, len(imageData)),
	}
	for _, item := range imageData {
		if item.Url == "" && item.B64Json == "" {
			continue
		}
		imageResponse.Data = append(imageResponse.Data, item)
	}
	if len(imageResponse.Data) == 0 {
		return nil, types.NewOpenAIError(fmt.Errorf("runninghub returned no usable images"), types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}

	c.JSON(http.StatusOK, imageResponse)
	return &dto.Usage{}, nil
}

func pollRunningHubImageData(info *relaycommon.RelayInfo, taskID string) ([]dto.ImageData, error) {
	client, err := service.GetHttpClientWithProxy(info.ChannelSetting.Proxy)
	if err != nil {
		return nil, fmt.Errorf("new proxy http client failed: %w", err)
	}

	queryURL := runningHubQueryURL(info.ChannelBaseUrl)
	bodyBytes, err := common.Marshal(map[string]any{"taskId": taskID})
	if err != nil {
		return nil, fmt.Errorf("marshal runninghub query body failed: %w", err)
	}

	for attempt := 0; attempt < runningHubPollMaxRetry; attempt++ {
		if attempt == 0 {
			time.Sleep(1500 * time.Millisecond)
		} else {
			time.Sleep(runningHubPollInterval)
		}

		req, err := http.NewRequest(http.MethodPost, queryURL, bytes.NewReader(bodyBytes))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+info.ApiKey)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			if attempt == runningHubPollMaxRetry-1 {
				return nil, err
			}
			continue
		}

		responseBody, readErr := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		if readErr != nil {
			if attempt == runningHubPollMaxRetry-1 {
				return nil, readErr
			}
			continue
		}

		if resp.StatusCode >= 400 {
			return nil, fmt.Errorf("runninghub query failed (%d): %s", resp.StatusCode, strings.TrimSpace(string(responseBody)))
		}

		var payload map[string]any
		if err := common.Unmarshal(responseBody, &payload); err != nil {
			continue
		}

		status := runningHubStatus(payload)
		imageData := runningHubImageData(payload)
		if len(imageData) > 0 && (status == "" || strings.Contains(status, "success") || strings.Contains(status, "complete") || strings.Contains(status, "done") || strings.Contains(status, "finished")) {
			return imageData, nil
		}
		if strings.Contains(status, "fail") || strings.Contains(status, "error") || strings.Contains(status, "cancel") || strings.Contains(status, "timeout") {
			message := runningHubMessage(payload)
			if message == "" {
				message = status
			}
			return nil, fmt.Errorf("%s", message)
		}
	}
	return nil, fmt.Errorf("runninghub polling timeout")
}
