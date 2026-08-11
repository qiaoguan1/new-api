package middleware

import (
	"bytes"
	"io"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

const XingTuVideoMaxRequestBytes = 256 * 1024

// XingTuVideoBodyLimit bounds every XingTu-tagged create payload before
// authentication distribution, JSON decoding, billing, or provider effects.
func XingTuVideoBodyLimit() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.Method != http.MethodPost || c.Request.URL.Path != "/v1/videos" ||
			strings.TrimSpace(c.GetHeader("X-XingTu-Contract-Version")) == "" {
			c.Next()
			return
		}

		if c.Request.ContentLength > XingTuVideoMaxRequestBytes {
			_ = c.Request.Body.Close()
			xingTuVideoBodyError(c, http.StatusRequestEntityTooLarge, "request_too_large", "request body exceeds 256 KiB")
			return
		}
		body, err := io.ReadAll(io.LimitReader(c.Request.Body, XingTuVideoMaxRequestBytes+1))
		_ = c.Request.Body.Close()
		if err != nil {
			xingTuVideoBodyError(c, http.StatusBadRequest, "invalid_request", "unable to read request body")
			return
		}
		if len(body) > XingTuVideoMaxRequestBytes {
			xingTuVideoBodyError(c, http.StatusRequestEntityTooLarge, "request_too_large", "request body exceeds 256 KiB")
			return
		}
		c.Request.Body = io.NopCloser(bytes.NewReader(body))
		c.Request.ContentLength = int64(len(body))
		c.Next()
	}
}

func xingTuVideoBodyError(c *gin.Context, status int, code, message string) {
	c.Header("Cache-Control", "private, no-store")
	c.AbortWithStatusJSON(status, gin.H{"error": gin.H{
		"code": code, "message": message, "retryable": false,
	}})
}
