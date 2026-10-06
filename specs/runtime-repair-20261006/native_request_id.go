package middleware

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"runtime/debug"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

// Preserve the existing production request-ID generator and build fingerprint.
var _bp = func() string {
	if bi, ok := debug.ReadBuildInfo(); ok && bi.Main.Path != "" {
		h := sha256.Sum256([]byte(bi.Main.Path))
		return hex.EncodeToString(h[:4])
	}
	return common.GetRandomString(8)
}()

func RequestId() func(c *gin.Context) {
	return func(c *gin.Context) {
		id := common.GetTimeString() + _bp + common.GetRandomString(8)
		c.Set(common.RequestIdKey, id)
		ctx := context.WithValue(c.Request.Context(), common.RequestIdKey, id)
		c.Request = c.Request.WithContext(ctx)
		c.Header(common.RequestIdKey, id)
		c.Header(common.ImageRelayRequestIDHeader, id)
		if c.Request.URL.Path == "/v1/images/generations" || c.Request.URL.Path == "/v1/images/edits" {
			c.Header(common.ImageSubmissionStateHeader, "not_submitted")
			clientID := c.Request.Header.Get("X-Request-ID")
			if strings.HasPrefix(clientID, "btask_") && len(clientID) == 38 {
				if _, err := hex.DecodeString(clientID[6:]); err == nil {
					c.Set(common.ImageClientRequestIDContext, clientID)
				}
			}
		}
		c.Next()
	}
}
