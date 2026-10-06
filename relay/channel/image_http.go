package channel

import (
	"net/http"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

// imageRequestClient keeps the shared connection pool but adds a bounded
// image-only deadline. Never alter the shared client or truncate text streams.
func imageRequestClient(c *gin.Context, req *http.Request, client *http.Client) (*http.Request, *http.Client) {
	if c == nil || c.Request == nil || !service.IsImageRequestPath(c.Request.URL.Path) {
		return req, client
	}
	c.Set("xtai_image_submit_started", true)
	c.Header(common.ImageSubmissionStateHeader, "submitted")
	request := req.WithContext(c.Request.Context())
	copy := *client
	if copy.Timeout <= 0 || copy.Timeout > 600*time.Second {
		copy.Timeout = 600 * time.Second
	}
	return request, &copy
}
