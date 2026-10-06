package service

import (
	"errors"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

func IsImageRequestPath(path string) bool {
	return path == "/v1/images/generations" || path == "/v1/images/edits"
}

// IsSafeImageRejection permits failover only after a definite pre-generation
// rejection. Transport errors, 5xx, body errors and unknown results must not
// cause another image submission (the upstream may already have charged).
func IsSafeImageRejection(err *types.NewAPIError) bool {
	if err == nil {
		return false
	}
	if types.IsSkipRetryError(err) || strings.HasPrefix(string(err.GetErrorCode()), "violation_fee.") {
		return false
	}
	if err.GetErrorCode() == types.ErrorCodeChannelNoAvailableKey {
		return true
	}
	if err.StatusCode == http.StatusUnauthorized || err.StatusCode == http.StatusForbidden || err.StatusCode == http.StatusNotFound {
		return true
	}
	if err.StatusCode != http.StatusTooManyRequests && err.StatusCode != http.StatusBadRequest && err.StatusCode != http.StatusUnprocessableEntity {
		return false
	}
	message := strings.ToLower(err.Error())
	for _, marker := range []string{"no available image quota", "insufficient quota", "insufficient balance", "endpoint_requires_other_route", "model_not_supported", "resolution_not_supported", "resolution_requires_other_route", "size_requires_other_route", "quality_requires_other_route", "reference_requires_other_route", "extended_options_require_other_route", "unsupported_count_no_submit", "unsupported_model_no_submit", "upstream_rejected_no_task"} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}

func NormalizeImageSubmissionError(c *gin.Context, err *types.NewAPIError) *types.NewAPIError {
	if err == nil || c == nil || c.Request == nil || !IsImageRequestPath(c.Request.URL.Path) {
		return err
	}
	if IsSafeImageRejection(err) {
		c.Header(common.ImageSubmissionStateHeader, "rejected_no_task")
		return err
	}
	if types.IsChannelError(err) {
		return err
	}
	if !c.GetBool("xtai_image_submit_started") {
		return err
	}
	uncertain := err.GetErrorCode() == types.ErrorCodeDoRequestFailed || err.GetErrorCode() == types.ErrorCodeReadResponseBodyFailed || err.GetErrorCode() == types.ErrorCodeBadResponseBody || err.GetErrorCode() == types.ErrorCodeEmptyResponse
	uncertain = uncertain || err.StatusCode == 408 || err.StatusCode == 425 || err.StatusCode >= 500
	if !uncertain {
		return err
	}
	c.Header(common.ImageSubmissionStateHeader, "uncertain")
	return types.NewErrorWithStatusCode(err, types.ErrorCode("image_submit_uncertain"), err.StatusCode, types.ErrOptionWithSkipRetry())
}

type imageRouteKey struct {
	channel     int
	model, path string
}

var imageRouteCooldown = struct {
	sync.Mutex
	until map[imageRouteKey]time.Time
}{until: make(map[imageRouteKey]time.Time)}

func RecordImageRouteRejection(c *gin.Context, channelID int, modelName string, err *types.NewAPIError) {
	if c == nil || c.Request == nil || !IsImageRequestPath(c.Request.URL.Path) || !IsSafeImageRejection(err) {
		return
	}
	// A parameter mismatch excludes the route for this request only. Never let
	// a 4K/quality/count rejection disable the provider's subsequent valid 1K.
	imageRequestExclusions(c)[channelID] = true
	message := strings.ToLower(err.Error())
	quotaFailure := (err.StatusCode == 429 || err.StatusCode == 402) && (strings.Contains(message, "no available image quota") || strings.Contains(message, "insufficient quota") || strings.Contains(message, "insufficient balance"))
	if !quotaFailure && err.StatusCode != http.StatusUnauthorized && err.GetErrorCode() != types.ErrorCodeChannelNoAvailableKey {
		return
	}
	key := imageRouteKey{channelID, modelName, c.Request.URL.Path}
	now := time.Now()
	imageRouteCooldown.Lock()
	defer imageRouteCooldown.Unlock()
	for old, until := range imageRouteCooldown.until {
		if !until.After(now) {
			delete(imageRouteCooldown.until, old)
		}
	}
	imageRouteCooldown.until[key] = now.Add(15 * time.Minute)
}

func imageRequestExclusions(c *gin.Context) map[int]bool {
	if c != nil {
		if value, exists := c.Get("image_route_exclusions"); exists {
			if ids, ok := value.(map[int]bool); ok {
				return ids
			}
		}
	}
	ids := make(map[int]bool)
	if c != nil {
		c.Set("image_route_exclusions", ids)
	}
	return ids
}

func ImageChannelAllowed(c *gin.Context, channel *model.Channel, modelName string) bool {
	if c == nil || c.Request == nil || channel == nil {
		return channel != nil
	}
	path := c.Request.URL.Path
	if !IsImageRequestPath(path) {
		return true
	}
	setting := channel.GetSetting()
	if len(setting.ImageEndpoints) > 0 {
		allowed := false
		for _, endpoint := range setting.ImageEndpoints {
			if endpoint == path {
				allowed = true
				break
			}
		}
		if !allowed {
			return false
		}
	}
	if len(setting.ImageSizes) > 0 {
		size, found := c.Get("image_route_requested_size")
		if !found {
			var request struct {
				Size string `json:"size" form:"size"`
			}
			if common.UnmarshalBodyReusable(c, &request) != nil {
				return false
			}
			size = request.Size
			if request.Size == "" || request.Size == "auto" || strings.EqualFold(request.Size, "1k") {
				size = "1024x1024"
			}
			c.Set("image_route_requested_size", size)
		}
		allowed := false
		for _, supported := range setting.ImageSizes {
			if supported == size {
				allowed = true
				break
			}
		}
		if !allowed {
			return false
		}
	}
	imageRouteCooldown.Lock()
	until := imageRouteCooldown.until[imageRouteKey{channel.Id, modelName, path}]
	imageRouteCooldown.Unlock()
	return !until.After(time.Now())
}

// SelectImageRoute filters capability/cooldown before an HTTP submission and
// before priority selection can charge an incompatible route. Exclusions are
// request-scoped; a provider rejection never disables its text/video routes.
func SelectImageRoute(c *gin.Context, modelName string, excluded map[int]bool, choose func(map[int]bool) (*model.Channel, error)) (*model.Channel, error) {
	if excluded == nil {
		excluded = imageRequestExclusions(c)
	}
	for attempts := 0; attempts < 256; attempts++ {
		channel, err := choose(excluded)
		if channel == nil || err != nil {
			return channel, err
		}
		if excluded[channel.Id] {
			return nil, errors.New("image channel selector did not honor exclusions")
		}
		if ImageChannelAllowed(c, channel, modelName) {
			return channel, nil
		}
		excluded[channel.Id] = true
	}
	return nil, errors.New("image channel selection limit exceeded")
}
