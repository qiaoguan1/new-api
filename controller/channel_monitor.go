package controller

import (
	"encoding/json"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
)

const channelMonitorDataPath = "/channel-monitor-data/monitor-data.json"

func GetChannelMonitor(c *gin.Context) {
	payload, err := os.ReadFile(channelMonitorDataPath)
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"success": false, "message": "渠道监控数据暂不可用"})
		return
	}
	if !json.Valid(payload) {
		c.JSON(http.StatusInternalServerError, gin.H{"success": false, "message": "渠道监控数据格式错误"})
		return
	}
	c.Data(http.StatusOK, "application/json; charset=utf-8", payload)
}
