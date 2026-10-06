package model

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

// Client job IDs supplement, but never replace, authenticated account/token and
// server-generated request IDs. They are not authorization or refund evidence.
func appendImageClientAudit(c *gin.Context, original map[string]interface{}) map[string]interface{} {
	if c == nil || c.GetString(common.ImageClientRequestIDContext) == "" {
		return original
	}
	copy := make(map[string]interface{}, len(original)+1)
	for key, value := range original {
		copy[key] = value
	}
	copy["client_image_request_id"] = c.GetString(common.ImageClientRequestIDContext)
	return copy
}
