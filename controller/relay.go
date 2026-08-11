package controller

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	perfmetrics "github.com/QuantumNous/new-api/pkg/perf_metrics"
	"github.com/QuantumNous/new-api/relay"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relay/helper"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/QuantumNous/new-api/types"

	"github.com/bytedance/gopkg/util/gopool"
	"github.com/samber/lo"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

func relayHandler(c *gin.Context, info *relaycommon.RelayInfo) *types.NewAPIError {
	var err *types.NewAPIError
	switch info.RelayMode {
	case relayconstant.RelayModeImagesGenerations, relayconstant.RelayModeImagesEdits:
		err = relay.ImageHelper(c, info)
	case relayconstant.RelayModeAudioSpeech:
		fallthrough
	case relayconstant.RelayModeAudioTranslation:
		fallthrough
	case relayconstant.RelayModeAudioTranscription:
		err = relay.AudioHelper(c, info)
	case relayconstant.RelayModeRerank:
		err = relay.RerankHelper(c, info)
	case relayconstant.RelayModeEmbeddings:
		err = relay.EmbeddingHelper(c, info)
	case relayconstant.RelayModeResponses, relayconstant.RelayModeResponsesCompact:
		err = relay.ResponsesHelper(c, info)
	default:
		err = relay.TextHelper(c, info)
	}
	return err
}

func geminiRelayHandler(c *gin.Context, info *relaycommon.RelayInfo) *types.NewAPIError {
	var err *types.NewAPIError
	if strings.Contains(c.Request.URL.Path, "embed") {
		err = relay.GeminiEmbeddingHandler(c, info)
	} else {
		err = relay.GeminiHelper(c, info)
	}
	return err
}

func Relay(c *gin.Context, relayFormat types.RelayFormat) {

	requestId := c.GetString(common.RequestIdKey)
	//group := common.GetContextKeyString(c, constant.ContextKeyUsingGroup)
	//originalModel := common.GetContextKeyString(c, constant.ContextKeyOriginalModel)

	var (
		newAPIError *types.NewAPIError
		ws          *websocket.Conn
	)

	if relayFormat == types.RelayFormatOpenAIRealtime {
		var err error
		ws, err = upgrader.Upgrade(c.Writer, c.Request, nil)
		if err != nil {
			helper.WssError(c, ws, types.NewError(err, types.ErrorCodeGetChannelFailed, types.ErrOptionWithSkipRetry()).ToOpenAIError())
			return
		}
		defer ws.Close()
	}

	defer func() {
		if newAPIError != nil {
			logger.LogError(c, fmt.Sprintf("relay error: %s", newAPIError.Error()))
			newAPIError.SetMessage(common.MessageWithRequestId(newAPIError.Error(), requestId))
			switch relayFormat {
			case types.RelayFormatOpenAIRealtime:
				helper.WssError(c, ws, newAPIError.ToOpenAIError())
			case types.RelayFormatClaude:
				c.JSON(newAPIError.StatusCode, gin.H{
					"type":  "error",
					"error": newAPIError.ToClaudeError(),
				})
			default:
				c.JSON(newAPIError.StatusCode, gin.H{
					"error": newAPIError.ToOpenAIError(),
				})
			}
		}
	}()

	request, err := helper.GetAndValidateRequest(c, relayFormat)
	if err != nil {
		// Map "request body too large" to 413 so clients can handle it correctly
		if common.IsRequestBodyTooLargeError(err) || errors.Is(err, common.ErrRequestBodyTooLarge) {
			newAPIError = types.NewErrorWithStatusCode(err, types.ErrorCodeReadRequestBodyFailed, http.StatusRequestEntityTooLarge, types.ErrOptionWithSkipRetry())
		} else {
			newAPIError = types.NewError(err, types.ErrorCodeInvalidRequest)
		}
		return
	}

	relayInfo, err := relaycommon.GenRelayInfo(c, relayFormat, request, ws)
	if err != nil {
		newAPIError = types.NewError(err, types.ErrorCodeGenRelayInfoFailed)
		return
	}

	needSensitiveCheck := setting.ShouldCheckPromptSensitive()
	needCountToken := constant.CountToken
	// Avoid building huge CombineText (strings.Join) when token counting and sensitive check are both disabled.
	var meta *types.TokenCountMeta
	if needSensitiveCheck || needCountToken {
		meta = request.GetTokenCountMeta()
	} else {
		meta = fastTokenCountMetaForPricing(request)
	}

	if needSensitiveCheck && meta != nil {
		contains, words := service.CheckSensitiveText(meta.CombineText)
		if contains {
			logger.LogWarn(c, fmt.Sprintf("user sensitive words detected: %s", strings.Join(words, ", ")))
			newAPIError = types.NewError(err, types.ErrorCodeSensitiveWordsDetected)
			return
		}
	}

	tokens, err := service.EstimateRequestToken(c, meta, relayInfo)
	if err != nil {
		newAPIError = types.NewError(err, types.ErrorCodeCountTokenFailed)
		return
	}

	relayInfo.SetEstimatePromptTokens(tokens)

	priceData, err := helper.ModelPriceHelper(c, relayInfo, tokens, meta)
	if err != nil {
		newAPIError = types.NewError(err, types.ErrorCodeModelPriceError, types.ErrOptionWithStatusCode(http.StatusBadRequest))
		return
	}

	// common.SetContextKey(c, constant.ContextKeyTokenCountMeta, meta)

	if priceData.FreeModel {
		logger.LogInfo(c, fmt.Sprintf("模型 %s 免费，跳过预扣费", relayInfo.OriginModelName))
	} else {
		newAPIError = service.PreConsumeBilling(c, priceData.QuotaToPreConsume, relayInfo)
		if newAPIError != nil {
			return
		}
	}

	defer func() {
		// Only return quota if downstream failed and quota was actually pre-consumed
		if newAPIError != nil {
			newAPIError = service.NormalizeViolationFeeError(newAPIError)
			if relayInfo.Billing != nil {
				relayInfo.Billing.Refund(c)
			}
			service.ChargeViolationFeeIfNeeded(c, relayInfo, newAPIError)
		}
	}()

	retryParam := &service.RetryParam{
		Ctx:        c,
		TokenGroup: relayInfo.TokenGroup,
		ModelName:  relayInfo.OriginModelName,
		Retry:      common.GetPointer(0),
	}
	relayInfo.RetryIndex = 0
	relayInfo.LastError = nil
	excludedChannels := make(map[int]bool)

	for {
		relayInfo.RetryIndex = retryParam.GetRetry()
		channel, channelErr := getChannel(c, relayInfo, retryParam, excludedChannels)
		if channelErr != nil {
			logger.LogError(c, channelErr.Error())
			newAPIError = channelErr
			break
		}
		if channel == nil {
			newAPIError = types.NewError(fmt.Errorf("????????????? %s ?????", relayInfo.OriginModelName), types.ErrorCodeGetChannelFailed, types.ErrOptionWithSkipRetry())
			break
		}

		addUsedChannel(c, channel.Id)
		bodyStorage, bodyErr := common.GetBodyStorage(c)
		if bodyErr != nil {
			if common.IsRequestBodyTooLargeError(bodyErr) || errors.Is(bodyErr, common.ErrRequestBodyTooLarge) {
				newAPIError = types.NewErrorWithStatusCode(bodyErr, types.ErrorCodeReadRequestBodyFailed, http.StatusRequestEntityTooLarge, types.ErrOptionWithSkipRetry())
			} else {
				newAPIError = types.NewErrorWithStatusCode(bodyErr, types.ErrorCodeReadRequestBodyFailed, http.StatusBadRequest, types.ErrOptionWithSkipRetry())
			}
			break
		}
		c.Request.Body = io.NopCloser(bodyStorage)

		switch relayFormat {
		case types.RelayFormatOpenAIRealtime:
			newAPIError = relay.WssHelper(c, relayInfo)
		case types.RelayFormatClaude:
			newAPIError = relay.ClaudeHelper(c, relayInfo)
		case types.RelayFormatGemini:
			newAPIError = geminiRelayHandler(c, relayInfo)
		default:
			newAPIError = relayHandler(c, relayInfo)
		}

		if newAPIError == nil {
			relayInfo.LastError = nil
			return
		}

		newAPIError = service.NormalizeViolationFeeError(newAPIError)
		relayInfo.LastError = newAPIError

		processChannelError(c, *types.NewChannelError(channel.Id, channel.Type, channel.Name, channel.ChannelInfo.IsMultiKey, common.GetContextKeyString(c, constant.ContextKeyChannelKey), channel.GetAutoBan()), newAPIError)
		excludedChannels[channel.Id] = true
	}
	useChannel := c.GetStringSlice("use_channel")
	if len(useChannel) > 1 {
		retryLogStr := fmt.Sprintf("重试：%s", strings.Trim(strings.Join(strings.Fields(fmt.Sprint(useChannel)), "->"), "[]"))
		logger.LogInfo(c, retryLogStr)
	}
	if newAPIError != nil {
		gopool.Go(func() {
			perfmetrics.RecordRelaySample(relayInfo, false, 0)
		})
	}
}

var upgrader = websocket.Upgrader{
	Subprotocols: []string{"realtime"}, // WS 握手支持的协议，如果有使用 Sec-WebSocket-Protocol，则必须在此声明对应的 Protocol TODO add other protocol
	CheckOrigin: func(r *http.Request) bool {
		return true // 允许跨域
	},
}

func addUsedChannel(c *gin.Context, channelId int) {
	useChannel := c.GetStringSlice("use_channel")
	useChannel = append(useChannel, fmt.Sprintf("%d", channelId))
	c.Set("use_channel", useChannel)
}

func fastTokenCountMetaForPricing(request dto.Request) *types.TokenCountMeta {
	if request == nil {
		return &types.TokenCountMeta{}
	}
	meta := &types.TokenCountMeta{
		TokenType: types.TokenTypeTokenizer,
	}
	switch r := request.(type) {
	case *dto.GeneralOpenAIRequest:
		maxCompletionTokens := lo.FromPtrOr(r.MaxCompletionTokens, uint(0))
		maxTokens := lo.FromPtrOr(r.MaxTokens, uint(0))
		if maxCompletionTokens > maxTokens {
			meta.MaxTokens = int(maxCompletionTokens)
		} else {
			meta.MaxTokens = int(maxTokens)
		}
	case *dto.OpenAIResponsesRequest:
		meta.MaxTokens = int(lo.FromPtrOr(r.MaxOutputTokens, uint(0)))
	case *dto.ClaudeRequest:
		meta.MaxTokens = int(lo.FromPtr(r.MaxTokens))
	case *dto.ImageRequest:
		// Pricing for image requests depends on ImagePriceRatio; safe to compute even when CountToken is disabled.
		return r.GetTokenCountMeta()
	default:
		// Best-effort: leave CombineText empty to avoid large allocations.
	}
	return meta
}

func getChannel(c *gin.Context, info *relaycommon.RelayInfo, retryParam *service.RetryParam, excluded map[int]bool) (*model.Channel, *types.NewAPIError) {
	if info.ChannelMeta == nil {
		autoBan := c.GetBool("auto_ban")
		autoBanInt := 1
		if !autoBan {
			autoBanInt = 0
		}
		return &model.Channel{
			Id:      c.GetInt("channel_id"),
			Type:    c.GetInt("channel_type"),
			Name:    c.GetString("channel_name"),
			AutoBan: &autoBanInt,
		}, nil
	}
	channel, selectGroup, err := service.CacheGetRandomSatisfiedChannel(retryParam, excluded)

	info.PriceData.GroupRatioInfo = helper.HandleGroupRatio(c, info)

	if err != nil {
		return nil, types.NewError(fmt.Errorf("???? %s ??? %s ????????retry?: %s", selectGroup, info.OriginModelName, err.Error()), types.ErrorCodeGetChannelFailed, types.ErrOptionWithSkipRetry())
	}
	if channel == nil {
		return nil, types.NewError(fmt.Errorf("?? %s ??? %s ?????????retry?", selectGroup, info.OriginModelName), types.ErrorCodeGetChannelFailed, types.ErrOptionWithSkipRetry())
	}

	newAPIError := middleware.SetupContextForSelectedChannel(c, channel, info.OriginModelName)
	if newAPIError != nil {
		return nil, newAPIError
	}
	return channel, nil
}

func shouldRetry(c *gin.Context, openaiErr *types.NewAPIError, retryTimes int) bool {
	if openaiErr == nil {
		return false
	}
	if service.ShouldSkipRetryAfterChannelAffinityFailure(c) {
		return false
	}
	if types.IsChannelError(openaiErr) {
		return true
	}
	if types.IsSkipRetryError(openaiErr) {
		return false
	}
	if retryTimes <= 0 {
		return false
	}
	if _, ok := c.Get("specific_channel_id"); ok {
		return false
	}
	code := openaiErr.StatusCode
	if code >= 200 && code < 300 {
		return false
	}
	if code < 100 || code > 599 {
		return true
	}
	if operation_setting.IsAlwaysSkipRetryCode(openaiErr.GetErrorCode()) {
		return false
	}
	return operation_setting.ShouldRetryByStatusCode(code)
}

func processChannelError(c *gin.Context, channelError types.ChannelError, err *types.NewAPIError) {
	logger.LogError(c, fmt.Sprintf("channel error (channel #%d, status code: %d): %s", channelError.ChannelId, err.StatusCode, err.Error()))
	// 不要使用context获取渠道信息，异步处理时可能会出现渠道信息不一致的情况
	// do not use context to get channel info, there may be inconsistent channel info when processing asynchronously
	if service.ShouldDisableChannel(err) && channelError.AutoBan {
		gopool.Go(func() {
			service.DisableChannel(channelError, err.ErrorWithStatusCode())
		})
	}

	if constant.ErrorLogEnabled && types.IsRecordErrorLog(err) {
		// 保存错误日志到mysql中
		userId := c.GetInt("id")
		tokenName := c.GetString("token_name")
		modelName := c.GetString("original_model")
		tokenId := c.GetInt("token_id")
		userGroup := c.GetString("group")
		channelId := c.GetInt("channel_id")
		other := make(map[string]interface{})
		if c.Request != nil && c.Request.URL != nil {
			other["request_path"] = c.Request.URL.Path
		}
		other["error_type"] = err.GetErrorType()
		other["error_code"] = err.GetErrorCode()
		other["status_code"] = err.StatusCode
		other["channel_id"] = channelId
		other["channel_name"] = c.GetString("channel_name")
		other["channel_type"] = c.GetInt("channel_type")
		adminInfo := make(map[string]interface{})
		adminInfo["use_channel"] = c.GetStringSlice("use_channel")
		isMultiKey := common.GetContextKeyBool(c, constant.ContextKeyChannelIsMultiKey)
		if isMultiKey {
			adminInfo["is_multi_key"] = true
			adminInfo["multi_key_index"] = common.GetContextKeyInt(c, constant.ContextKeyChannelMultiKeyIndex)
		}
		service.AppendChannelAffinityAdminInfo(c, adminInfo)
		other["admin_info"] = adminInfo
		startTime := common.GetContextKeyTime(c, constant.ContextKeyRequestStartTime)
		if startTime.IsZero() {
			startTime = time.Now()
		}
		useTimeSeconds := int(time.Since(startTime).Seconds())
		model.RecordErrorLog(c, userId, channelId, modelName, tokenName, err.MaskSensitiveErrorWithStatusCode(), tokenId, useTimeSeconds, common.GetContextKeyBool(c, constant.ContextKeyIsStream), userGroup, other)
	}

}

func RelayMidjourney(c *gin.Context) {
	relayInfo, err := relaycommon.GenRelayInfo(c, types.RelayFormatMjProxy, nil, nil)

	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"description": fmt.Sprintf("failed to generate relay info: %s", err.Error()),
			"type":        "upstream_error",
			"code":        4,
		})
		return
	}

	var mjErr *dto.MidjourneyResponse
	switch relayInfo.RelayMode {
	case relayconstant.RelayModeMidjourneyNotify:
		mjErr = relay.RelayMidjourneyNotify(c)
	case relayconstant.RelayModeMidjourneyTaskFetch, relayconstant.RelayModeMidjourneyTaskFetchByCondition:
		mjErr = relay.RelayMidjourneyTask(c, relayInfo.RelayMode)
	case relayconstant.RelayModeMidjourneyTaskImageSeed:
		mjErr = relay.RelayMidjourneyTaskImageSeed(c)
	case relayconstant.RelayModeSwapFace:
		mjErr = relay.RelaySwapFace(c, relayInfo)
	default:
		mjErr = relay.RelayMidjourneySubmit(c, relayInfo)
	}
	//err = relayMidjourneySubmit(c, relayMode)
	log.Println(mjErr)
	if mjErr != nil {
		statusCode := http.StatusBadRequest
		if mjErr.Code == 30 {
			mjErr.Result = "当前分组负载已饱和，请稍后再试，或升级账户以提升服务质量。"
			statusCode = http.StatusTooManyRequests
		}
		c.JSON(statusCode, gin.H{
			"description": fmt.Sprintf("%s %s", mjErr.Description, mjErr.Result),
			"type":        "upstream_error",
			"code":        mjErr.Code,
		})
		channelId := c.GetInt("channel_id")
		logger.LogError(c, fmt.Sprintf("relay error (channel #%d, status code %d): %s", channelId, statusCode, fmt.Sprintf("%s %s", mjErr.Description, mjErr.Result)))
	}
}

func RelayNotImplemented(c *gin.Context) {
	err := types.OpenAIError{
		Message: "API not implemented",
		Type:    "new_api_error",
		Param:   "",
		Code:    "api_not_implemented",
	}
	c.JSON(http.StatusNotImplemented, gin.H{
		"error": err,
	})
}

func RelayNotFound(c *gin.Context) {
	err := types.OpenAIError{
		Message: fmt.Sprintf("Invalid URL (%s %s)", c.Request.Method, c.Request.URL.Path),
		Type:    "invalid_request_error",
		Param:   "",
		Code:    "",
	}
	c.JSON(http.StatusNotFound, gin.H{
		"error": err,
	})
}

func RelayTaskFetch(c *gin.Context) {
	if c.Request.URL.Path != "" && strings.HasPrefix(c.Request.URL.Path, "/v1/videos/") {
		contractVersion := strings.TrimSpace(c.GetHeader(service.XingTuVideoContractHeader))
		if contractVersion != "" {
			c.Set(relaycommon.XingTuVideoContractContextKey, true)
			if contractVersion != service.XingTuVideoContractCurrent && contractVersion != service.XingTuVideoContractV2 {
				respondXingTuVideoError(c, http.StatusBadRequest, "unsupported_contract_version", "unsupported XingTu video contract version", false, "", c.Param("task_id"))
				return
			}
		}
	}
	relayInfo, err := relaycommon.GenRelayInfo(c, types.RelayFormatTask, nil, nil)
	if err != nil {
		respondTaskError(c, &dto.TaskError{
			Code:       "gen_relay_info_failed",
			Message:    err.Error(),
			StatusCode: http.StatusInternalServerError,
		})
		return
	}
	if taskErr := relay.RelayTaskFetch(c, relayInfo.RelayMode); taskErr != nil {
		respondTaskError(c, taskErr)
	}
}

func RelayTask(c *gin.Context) {
	if c.Request.URL.Path == "/v1/videos" && strings.TrimSpace(c.GetHeader(service.XingTuVideoContractHeader)) != "" {
		c.Set(relaycommon.XingTuVideoContractContextKey, true)
		if strings.TrimSpace(c.GetHeader(service.XingTuVideoContractHeader)) != service.XingTuVideoContractCurrent {
			respondXingTuVideoError(c, http.StatusBadRequest, "unsupported_contract_version", "unsupported XingTu video contract version", false, strings.TrimSpace(c.GetHeader("Idempotency-Key")), "")
			return
		}
	}
	relayInfo, err := relaycommon.GenRelayInfo(c, types.RelayFormatTask, nil, nil)
	if err != nil {
		respondTaskError(c, &dto.TaskError{
			Code:       "gen_relay_info_failed",
			Message:    err.Error(),
			StatusCode: http.StatusInternalServerError,
		})
		return
	}
	videoRequestClaim, handled := beginXingTuVideoRequest(c, relayInfo)
	if handled {
		return
	}

	if taskErr := relay.ResolveOriginTask(c, relayInfo); taskErr != nil {
		if videoRequestClaim != nil {
			_ = model.FailVideoRequestClaim(videoRequestClaim.ID, safeXingTuTaskErrorCode(taskErr), safeXingTuTaskErrorMessage(taskErr), false)
		}
		respondTaskError(c, taskErr)
		return
	}

	var result *relay.TaskSubmitResult
	var taskErr *dto.TaskError
	defer func() {
		if taskErr != nil && relayInfo.Billing != nil {
			relayInfo.Billing.Refund(c)
		}
	}()

	retryParam := &service.RetryParam{
		Ctx:        c,
		TokenGroup: relayInfo.TokenGroup,
		ModelName:  relayInfo.OriginModelName,
		Retry:      common.GetPointer(0),
	}

	for ; retryParam.GetRetry() <= common.RetryTimes; retryParam.IncreaseRetry() {
		var channel *model.Channel

		if lockedCh, ok := relayInfo.LockedChannel.(*model.Channel); ok && lockedCh != nil {
			channel = lockedCh
			if retryParam.GetRetry() > 0 {
				if setupErr := middleware.SetupContextForSelectedChannel(c, channel, relayInfo.OriginModelName); setupErr != nil {
					taskErr = service.TaskErrorWrapperLocal(setupErr.Err, "setup_locked_channel_failed", http.StatusInternalServerError)
					break
				}
			}
		} else {
			var channelErr *types.NewAPIError
			channel, channelErr = getChannel(c, relayInfo, retryParam, nil)
			if channelErr != nil {
				logger.LogError(c, channelErr.Error())
				taskErr = service.TaskErrorWrapperLocal(channelErr.Err, "get_channel_failed", http.StatusInternalServerError)
				break
			}
		}

		addUsedChannel(c, channel.Id)
		bodyStorage, bodyErr := common.GetBodyStorage(c)
		if bodyErr != nil {
			if common.IsRequestBodyTooLargeError(bodyErr) || errors.Is(bodyErr, common.ErrRequestBodyTooLarge) {
				taskErr = service.TaskErrorWrapperLocal(bodyErr, "read_request_body_failed", http.StatusRequestEntityTooLarge)
			} else {
				taskErr = service.TaskErrorWrapperLocal(bodyErr, "read_request_body_failed", http.StatusBadRequest)
			}
			break
		}
		c.Request.Body = io.NopCloser(bodyStorage)

		result, taskErr = relay.RelayTaskSubmit(c, relayInfo)
		if taskErr == nil {
			break
		}

		if !taskErr.LocalError {
			processChannelError(c,
				*types.NewChannelError(channel.Id, channel.Type, channel.Name, channel.ChannelInfo.IsMultiKey,
					common.GetContextKeyString(c, constant.ContextKeyChannelKey), channel.GetAutoBan()),
				types.NewOpenAIError(taskErr.Error, types.ErrorCodeBadResponseStatusCode, taskErr.StatusCode))
		}

		if !shouldRetryTaskRelay(c, channel.Id, taskErr, common.RetryTimes-retryParam.GetRetry()) {
			break
		}
	}

	useChannel := c.GetStringSlice("use_channel")
	if len(useChannel) > 1 {
		retryLogStr := fmt.Sprintf("重试：%s", strings.Trim(strings.Join(strings.Fields(fmt.Sprint(useChannel)), "->"), "[]"))
		logger.LogInfo(c, retryLogStr)
	}

	// ── 成功：结算 + 日志 + 插入任务 ──
	if taskErr == nil {
		videoBillingV2 := c.GetString(relay.VideoBillingContractContextKey) == service.VideoBillingContractVersion
		task := model.InitTask(result.Platform, relayInfo)
		if videoRequestClaim != nil {
			task.PrivateData.RequestID = videoRequestClaim.RequestID
			task.PrivateData.RequestFingerprint = videoRequestClaim.RequestFingerprint
		}
		task.PrivateData.UpstreamTaskID = result.UpstreamTaskID
		task.PrivateData.BillingSource = relayInfo.BillingSource
		task.PrivateData.SubscriptionId = relayInfo.SubscriptionId
		task.PrivateData.TokenId = relayInfo.TokenId
		task.PrivateData.BillingContext = &model.TaskBillingContext{
			ContractVersion:         c.GetString(relay.VideoBillingContractContextKey),
			ModelPrice:              relayInfo.PriceData.ModelPrice,
			GroupRatio:              relayInfo.PriceData.GroupRatioInfo.GroupRatio,
			ModelRatio:              relayInfo.PriceData.ModelRatio,
			OtherRatios:             relayInfo.PriceData.OtherRatios,
			OriginModelName:         relayInfo.OriginModelName,
			PerCallBilling:          common.StringsContains(constant.TaskPricePatches, relayInfo.OriginModelName) || relayInfo.PriceData.UsePrice,
			QuotaPerUnit:            common.QuotaPerUnit,
			BillingCurrency:         "CNY",
			BillingStatus:           "reserved",
			ReservedQuota:           result.Quota,
			OfficialPricingRevision: c.GetString(relay.VideoOfficialRevisionContextKey),
		}
		task.Quota = result.Quota
		task.Data = result.TaskData
		task.Action = relayInfo.Action
		if !videoBillingV2 {
			if settleErr := service.SettleBilling(c, relayInfo, result.Quota); settleErr != nil {
				common.SysError("settle task billing error: " + settleErr.Error())
			}
			service.LogTaskConsumption(c, relayInfo)
		}
		if insertErr := task.Insert(); insertErr != nil {
			common.SysError("insert task error: " + insertErr.Error())
			if videoBillingV2 && relayInfo.Billing != nil {
				relayInfo.Billing.Refund(c)
			}
			if videoRequestClaim != nil {
				_ = model.FailVideoRequestClaim(videoRequestClaim.ID, "task_persistence_failed", "task persistence failed after provider submission", true)
				respondXingTuVideoError(c, http.StatusInternalServerError, "task_persistence_failed", "task state is uncertain; do not submit with a new request_id", true, videoRequestClaim.RequestID, videoRequestClaim.TaskID)
			}
			return
		}
		if videoRequestClaim != nil {
			if completeErr := model.CompleteVideoRequestClaim(videoRequestClaim.ID); completeErr != nil {
				common.SysError("complete XingTu idempotency claim error: " + completeErr.Error())
			}
			c.JSON(http.StatusOK, task.ToXingTuVideo())
		}
		if videoBillingV2 {
			if settleErr := service.SettleBilling(c, relayInfo, result.Quota); settleErr != nil {
				common.SysError("settle task billing error: " + settleErr.Error())
			}
			service.LogTaskConsumption(c, relayInfo)
		}
	}

	if taskErr != nil {
		if videoRequestClaim != nil {
			uncertain := taskErr.StatusCode == http.StatusRequestTimeout || taskErr.StatusCode >= http.StatusInternalServerError
			_ = model.FailVideoRequestClaim(videoRequestClaim.ID, safeXingTuTaskErrorCode(taskErr), safeXingTuTaskErrorMessage(taskErr), uncertain)
		}
		respondTaskError(c, taskErr)
	}
}

func beginXingTuVideoRequest(c *gin.Context, relayInfo *relaycommon.RelayInfo) (*model.VideoRequestClaim, bool) {
	if c.Request.URL.Path != "/v1/videos" {
		return nil, false
	}
	contractVersion := strings.TrimSpace(c.GetHeader(service.XingTuVideoContractHeader))
	if contractVersion == "" {
		return nil, false
	}
	c.Set(relaycommon.XingTuVideoContractContextKey, true)
	if contractVersion != service.XingTuVideoContractCurrent {
		respondXingTuVideoError(c, http.StatusBadRequest, "unsupported_contract_version", "unsupported XingTu video contract version", false, "", "")
		return nil, true
	}
	request, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		respondXingTuVideoError(c, http.StatusBadRequest, "invalid_request", err.Error(), false, "", "")
		return nil, true
	}
	validation, contractErr := service.ValidateXingTuVideoRequest(request, c.GetHeader("Idempotency-Key"))
	if contractErr != nil {
		respondXingTuVideoError(c, contractErr.StatusCode, contractErr.Code, contractErr.Message, contractErr.Retryable, request.RequestID, "")
		return nil, true
	}
	if pricingErr := relay.ValidateOfficialVideoRequest(request.Model, request.Resolution, request.Duration); pricingErr != nil {
		respondXingTuVideoError(c, http.StatusBadRequest, "unsupported_video_sku", pricingErr.Error(), false, validation.RequestID, "")
		return nil, true
	}
	c.Set("xingtu_request_id", validation.RequestID)
	publicTaskID := model.GenerateTaskID()
	claim, created, claimErr := model.ClaimVideoRequest(relayInfo.UserId, validation.RequestID, validation.Fingerprint, publicTaskID)
	if errors.Is(claimErr, model.ErrVideoRequestIdempotencyConflict) {
		respondXingTuVideoError(c, http.StatusConflict, "idempotency_conflict", "request_id was already used with a different payload", false, validation.RequestID, "")
		return nil, true
	}
	if claimErr != nil {
		respondXingTuVideoError(c, http.StatusInternalServerError, "idempotency_store_failed", "unable to reserve request identity", true, validation.RequestID, "")
		return nil, true
	}
	c.Set("xingtu_task_id", claim.TaskID)
	if !created {
		if existingTask, exists, lookupErr := model.GetByTaskId(relayInfo.UserId, claim.TaskID); lookupErr == nil && exists {
			if claim.State != model.VideoRequestStateCompleted {
				_ = model.CompleteVideoRequestClaim(claim.ID)
			}
			c.JSON(http.StatusOK, existingTask.ToXingTuVideo())
			return nil, true
		}
		switch claim.State {
		case model.VideoRequestStateFailed:
			if claim.ErrorCode == string(types.ErrorCodeInsufficientUserQuota) {
				reopened, reopenErr := model.ReopenVideoRequestClaim(claim.ID, claim.ErrorCode)
				if reopenErr != nil {
					respondXingTuVideoError(c, http.StatusInternalServerError, "idempotency_store_failed", "unable to resume request after recharge", true, claim.RequestID, claim.TaskID)
					return nil, true
				}
				if reopened {
					claim.State = model.VideoRequestStateClaimed
					claim.ErrorCode = ""
					claim.ErrorMessage = ""
					relayInfo.PublicTaskID = claim.TaskID
					return claim, false
				}
				c.Header("Retry-After", "2")
				respondXingTuVideoError(c, http.StatusConflict, "request_in_progress", "the original request is being retried after recharge", true, claim.RequestID, claim.TaskID)
				return nil, true
			}
			respondXingTuVideoError(c, http.StatusConflict, claim.ErrorCode, claim.ErrorMessage, false, claim.RequestID, claim.TaskID)
		case model.VideoRequestStateUncertain:
			respondXingTuVideoError(c, http.StatusConflict, "request_uncertain", "the original submission is uncertain; do not create another request_id", false, claim.RequestID, claim.TaskID)
		default:
			if time.Now().Unix()-claim.UpdatedAt >= 600 {
				_ = model.FailVideoRequestClaim(claim.ID, "request_uncertain", "the original submission did not finish acceptance", true)
				respondXingTuVideoError(c, http.StatusConflict, "request_uncertain", "the original submission is uncertain; do not create another request_id", false, claim.RequestID, claim.TaskID)
				return nil, true
			}
			c.Header("Retry-After", "2")
			respondXingTuVideoError(c, http.StatusConflict, "request_in_progress", "the original request is still being accepted", true, claim.RequestID, claim.TaskID)
		}
		return nil, true
	}
	relayInfo.PublicTaskID = claim.TaskID
	return claim, false
}

// respondTaskError 统一输出 Task 错误响应（含 429 限流提示改写）
func respondTaskError(c *gin.Context, taskErr *dto.TaskError) {
	if taskErr.StatusCode == http.StatusTooManyRequests {
		taskErr.Message = "当前分组上游负载已饱和，请稍后再试"
	}
	if relaycommon.IsXingTuVideoContract(c) {
		requestID := c.GetString("xingtu_request_id")
		if requestID == "" {
			requestID = strings.TrimSpace(c.GetHeader("Idempotency-Key"))
		}
		taskID := c.GetString("xingtu_task_id")
		if taskID == "" {
			taskID = c.Param("task_id")
		}
		respondXingTuVideoError(c, taskErr.StatusCode, safeXingTuTaskErrorCode(taskErr), safeXingTuTaskErrorMessage(taskErr), taskErr.StatusCode >= 500 || taskErr.StatusCode == http.StatusTooManyRequests, requestID, taskID)
		return
	}
	c.JSON(taskErr.StatusCode, taskErr)
}

func safeXingTuTaskErrorCode(taskErr *dto.TaskError) string {
	if taskErr == nil {
		return "video_request_failed"
	}
	switch taskErr.Code {
	case "task_contract_mismatch", "account_in_debt", string(types.ErrorCodeInsufficientUserQuota):
		return taskErr.Code
	}
	switch taskErr.StatusCode {
	case http.StatusBadRequest:
		return "invalid_video_request"
	case http.StatusUnauthorized:
		return "authentication_failed"
	case http.StatusForbidden:
		return "access_denied"
	case http.StatusRequestTimeout:
		return "submission_timeout"
	case http.StatusConflict:
		return "task_conflict"
	case http.StatusTooManyRequests:
		return "rate_limited"
	default:
		if taskErr.StatusCode >= http.StatusInternalServerError {
			return "video_service_unavailable"
		}
		return "video_request_failed"
	}
}

func safeXingTuTaskErrorMessage(taskErr *dto.TaskError) string {
	if taskErr == nil {
		return "video request failed"
	}
	switch {
	case taskErr.StatusCode == http.StatusTooManyRequests:
		return "video service is busy; retry the same request_id later"
	case taskErr.StatusCode == http.StatusRequestTimeout:
		return "video submission timed out; do not create a new request_id"
	case taskErr.StatusCode >= http.StatusInternalServerError:
		return "video service temporarily failed"
	case taskErr.StatusCode == http.StatusBadRequest:
		return "video request was rejected"
	case taskErr.StatusCode == http.StatusUnauthorized:
		return "authentication failed"
	case taskErr.StatusCode == http.StatusForbidden:
		return "video request is not permitted"
	default:
		return "video request failed"
	}
}

func respondXingTuVideoError(c *gin.Context, status int, code, message string, retryable bool, requestID, taskID string) {
	if code == "" {
		code = "video_request_failed"
	}
	if message == "" {
		message = "video request failed"
	}
	c.JSON(status, dto.XingTuVideoErrorEnvelope{Error: dto.XingTuVideoPublicError{
		Code: code, Message: message, RequestID: requestID, TaskID: taskID, Retryable: retryable,
	}})
}

func shouldRetryTaskRelay(c *gin.Context, channelId int, taskErr *dto.TaskError, retryTimes int) bool {
	if taskErr == nil {
		return false
	}
	if service.ShouldSkipRetryAfterChannelAffinityFailure(c) {
		return false
	}
	if retryTimes <= 0 {
		return false
	}
	if _, ok := c.Get("specific_channel_id"); ok {
		return false
	}
	if taskErr.StatusCode == http.StatusTooManyRequests {
		return true
	}
	if taskErr.StatusCode == 307 {
		return true
	}
	if taskErr.StatusCode/100 == 5 {
		// 超时不重试
		if operation_setting.IsAlwaysSkipRetryStatusCode(taskErr.StatusCode) {
			return false
		}
		return true
	}
	if taskErr.StatusCode == http.StatusBadRequest {
		return false
	}
	if taskErr.StatusCode == 408 {
		// azure处理超时不重试
		return false
	}
	if taskErr.LocalError {
		return false
	}
	if taskErr.StatusCode/100 == 2 {
		return false
	}
	return true
}
