package relay

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"

	"github.com/QuantumNous/new-api/common"
	appconstant "github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relay/helper"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

const maxStandaloneSearchUpstreamResponseSize = 32 << 20

// SearchHelper relays the standalone Codex search contract. It deliberately
// shares the controller's central pre-consume/refund lifecycle and settles a
// successful call from locally estimated usage because the upstream search
// response does not contain token usage.
func SearchHelper(c *gin.Context, info *relaycommon.RelayInfo) *types.NewAPIError {
	info.InitChannelMeta(c)
	if backendURL := configuredStandaloneSearchBackendURL(); backendURL != "" {
		searchResponse, err := service.ExecuteSearxStandaloneSearch(c.Request.Context(), backendURL, searchRequest(info))
		if err != nil {
			return types.NewOpenAIError(err, types.ErrorCodeDoRequestFailed, http.StatusBadGateway, types.ErrOptionWithSkipRetry())
		}
		markStandaloneSearchInternal(info)
		c.JSON(http.StatusOK, searchResponse)
		settleStandaloneSearch(c, info)
		return nil
	}
	if !supportsStandaloneSearchAPIType(info.ApiType) {
		return types.NewErrorWithStatusCode(
			fmt.Errorf("standalone search is only supported by Codex or explicitly routed Advanced Custom channels"),
			types.ErrorCodeInvalidApiType,
			http.StatusBadRequest,
			types.ErrOptionWithSkipRetry(),
		)
	}

	request, ok := info.Request.(*dto.SearchRequest)
	if !ok {
		return types.NewErrorWithStatusCode(
			fmt.Errorf("invalid request type, expected dto.SearchRequest, got %T", info.Request),
			types.ErrorCodeInvalidRequest,
			http.StatusBadRequest,
			types.ErrOptionWithSkipRetry(),
		)
	}

	if info.ApiType == appconstant.APITypeAdvancedCustom {
		searchResponse, err := service.ExecuteSearxStandaloneSearch(c.Request.Context(), info.ChannelBaseUrl, request)
		if err != nil {
			return types.NewOpenAIError(err, types.ErrorCodeDoRequestFailed, http.StatusBadGateway, types.ErrOptionWithSkipRetry())
		}
		c.JSON(http.StatusOK, searchResponse)
		settleStandaloneSearch(c, info)
		return nil
	}

	mappedRequest, err := common.DeepCopy(request)
	if err != nil {
		return types.NewError(fmt.Errorf("failed to copy search request: %w", err), types.ErrorCodeInvalidRequest, types.ErrOptionWithSkipRetry())
	}
	if err := helper.ModelMappedHelper(c, info, mappedRequest); err != nil {
		return types.NewError(err, types.ErrorCodeChannelModelMappedError, types.ErrOptionWithSkipRetry())
	}

	requestBody, err := common.Marshal(mappedRequest)
	if err != nil {
		return types.NewError(err, types.ErrorCodeConvertRequestFailed, types.ErrOptionWithSkipRetry())
	}
	if len(info.ParamOverride) > 0 {
		requestBody, err = relaycommon.ApplyParamOverrideWithRelayInfo(requestBody, info)
		if err != nil {
			return newAPIErrorFromParamOverride(err)
		}
	}

	adaptor := GetAdaptor(info.ApiType)
	if adaptor == nil {
		return types.NewError(fmt.Errorf("invalid api type: %d", info.ApiType), types.ErrorCodeInvalidApiType, types.ErrOptionWithSkipRetry())
	}
	adaptor.Init(info)
	upstream, err := adaptor.DoRequest(c, info, bytes.NewReader(requestBody))
	if err != nil {
		return types.NewOpenAIError(err, types.ErrorCodeDoRequestFailed, http.StatusInternalServerError)
	}
	response, ok := upstream.(*http.Response)
	if !ok || response == nil {
		return types.NewOpenAIError(fmt.Errorf("invalid standalone search response"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
	}
	if response.StatusCode != http.StatusOK {
		return service.RelayErrorHandler(c.Request.Context(), response, false)
	}

	_, responseBody, apiErr := readSearchResponse(response)
	if apiErr != nil {
		return apiErr
	}
	service.IOCopyBytesGracefully(c, response, responseBody)
	settleStandaloneSearch(c, info)
	return nil
}

func searchRequest(info *relaycommon.RelayInfo) *dto.SearchRequest {
	if info == nil {
		return nil
	}
	request, _ := info.Request.(*dto.SearchRequest)
	return request
}

func configuredStandaloneSearchBackendURL() string {
	return strings.TrimRight(strings.TrimSpace(os.Getenv("XT_STANDALONE_SEARCH_BASE_URL")), "/")
}

func markStandaloneSearchInternal(info *relaycommon.RelayInfo) {
	if info == nil || info.ChannelMeta == nil {
		return
	}
	info.ChannelId = 0
	info.ChannelType = 0
	info.ChannelBaseUrl = ""
	info.ApiKey = ""
	info.UpstreamModelName = info.OriginModelName
}

func settleStandaloneSearch(c *gin.Context, info *relaycommon.RelayInfo) {
	// Standalone search output is fed back into the following model request and
	// billed there as input. Charge one accounting token here plus the existing
	// web-search tool surcharge so the same content is not billed twice.
	usage := &dto.Usage{PromptTokens: 1, TotalTokens: 1}
	service.PostTextConsumeQuota(c, info, usage, []string{"Codex standalone web search"})
}

func supportsStandaloneSearchAPIType(apiType int) bool {
	return apiType == appconstant.APITypeCodex || apiType == appconstant.APITypeAdvancedCustom
}

func readSearchResponse(response *http.Response) (*dto.SearchResponse, []byte, *types.NewAPIError) {
	if response == nil || response.Body == nil {
		return nil, nil, types.NewOpenAIError(fmt.Errorf("empty standalone search response"), types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	defer service.CloseResponseBodyGracefully(response)
	body, err := io.ReadAll(io.LimitReader(response.Body, maxStandaloneSearchUpstreamResponseSize+1))
	if err != nil {
		return nil, nil, types.NewOpenAIError(err, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}
	if len(body) > maxStandaloneSearchUpstreamResponseSize {
		return nil, nil, types.NewOpenAIError(fmt.Errorf("standalone search response exceeds %d bytes", maxStandaloneSearchUpstreamResponseSize), types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}
	var envelope struct {
		Output *string `json:"output"`
	}
	if err := common.Unmarshal(body, &envelope); err != nil {
		return nil, nil, types.NewOpenAIError(err, types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	if envelope.Output == nil {
		return nil, nil, types.NewOpenAIError(fmt.Errorf("standalone search response is missing output"), types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	var parsed dto.SearchResponse
	if err := common.Unmarshal(body, &parsed); err != nil {
		return nil, nil, types.NewOpenAIError(err, types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	return &parsed, body, nil
}
