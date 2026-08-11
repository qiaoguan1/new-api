package middleware

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

func abortWithOpenAiMessage(c *gin.Context, statusCode int, message string, code ...types.ErrorCode) {
	codeStr := ""
	if len(code) > 0 {
		codeStr = string(code[0])
	}
	if isXingTuVideoContractRequest(c) {
		if codeStr == "" {
			switch statusCode {
			case http.StatusUnauthorized:
				codeStr = "authentication_failed"
			case http.StatusForbidden:
				codeStr = "access_denied"
			default:
				codeStr = "request_failed"
			}
		}
		c.JSON(statusCode, dto.XingTuVideoErrorEnvelope{Error: dto.XingTuVideoPublicError{
			Code:      codeStr,
			Message:   message,
			RequestID: strings.TrimSpace(c.GetHeader("Idempotency-Key")),
			Retryable: statusCode >= http.StatusInternalServerError || statusCode == http.StatusTooManyRequests,
		}})
		c.Abort()
		logger.LogError(c.Request.Context(), fmt.Sprintf("user %d | %s", c.GetInt("id"), message))
		return
	}
	userId := c.GetInt("id")
	c.JSON(statusCode, gin.H{
		"error": gin.H{
			"message": common.MessageWithRequestId(message, c.GetString(common.RequestIdKey)),
			"type":    "new_api_error",
			"code":    codeStr,
		},
	})
	c.Abort()
	logger.LogError(c.Request.Context(), fmt.Sprintf("user %d | %s", userId, message))
}

func isXingTuVideoContractRequest(c *gin.Context) bool {
	path := c.Request.URL.Path
	return (path == "/v1/videos" || strings.HasPrefix(path, "/v1/videos/")) &&
		service.IsXingTuVideoContractVersion(c.GetHeader(service.XingTuVideoContractHeader))
}

func abortWithMidjourneyMessage(c *gin.Context, statusCode int, code int, description string) {
	c.JSON(statusCode, gin.H{
		"description": description,
		"type":        "new_api_error",
		"code":        code,
	})
	c.Abort()
	logger.LogError(c.Request.Context(), description)
}
