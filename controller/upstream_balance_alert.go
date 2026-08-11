package controller

import (
	"fmt"
	"html"
	"math"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

const upstreamBalanceAlertTimeZone = "Asia/Shanghai"

var sendUpstreamBalanceAlertEmail = common.SendEmail

var upstreamBalanceAlertTitles = map[string]string{
	"balance_depleted":             "上游账户余额耗尽",
	"balance_depleted_reminder":    "上游账户余额仍未恢复",
	"balance_recovered":            "上游账户余额已恢复",
	"balance_collection_failed":    "上游余额监控连续采集失败",
	"balance_collection_recovered": "上游余额监控已恢复",
	"test":                         "上游余额监控测试邮件",
}

type upstreamBalanceAlertRequest struct {
	Kind       string   `json:"kind"`
	Name       string   `json:"name"`
	Balance    *float64 `json:"balance"`
	Threshold  float64  `json:"threshold"`
	OccurredAt int64    `json:"occurred_at"`
}

func validUpstreamBalanceAlertRequest(request upstreamBalanceAlertRequest) bool {
	if _, ok := upstreamBalanceAlertTitles[request.Kind]; !ok {
		return false
	}
	name := strings.TrimSpace(request.Name)
	if name == "" || utf8.RuneCountInString(name) > 80 ||
		strings.IndexFunc(name, unicode.IsControl) >= 0 {
		return false
	}
	if request.OccurredAt <= 0 || math.IsNaN(request.Threshold) || math.IsInf(request.Threshold, 0) {
		return false
	}
	if request.Balance != nil && (math.IsNaN(*request.Balance) || math.IsInf(*request.Balance, 0)) {
		return false
	}
	needsBalance := request.Kind == "balance_depleted" ||
		request.Kind == "balance_depleted_reminder" || request.Kind == "balance_recovered"
	return !needsBalance || request.Balance != nil
}

func upstreamBalanceAlertContent(request upstreamBalanceAlertRequest) (string, string) {
	title := upstreamBalanceAlertTitles[request.Kind]
	plainName := strings.TrimSpace(request.Name)
	name := html.EscapeString(plainName)
	balance := "未知"
	if request.Balance != nil {
		balance = fmt.Sprintf("%.6f", *request.Balance)
	}
	location, err := time.LoadLocation(upstreamBalanceAlertTimeZone)
	if err != nil {
		location = time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	timestamp := time.Unix(request.OccurredAt, 0).In(location).Format(time.RFC3339)
	subject := fmt.Sprintf("[星途监控] %s：%s", title, plainName)
	content := fmt.Sprintf(
		"<p>%s</p><p>上游渠道：%s<br>账户余额：%s（上游原始计费单位）<br>告警阈值：%.6f<br>北京时间：%s</p><p>本邮件不包含登录账号、密码、Token 或渠道接口地址。</p>",
		html.EscapeString(title), name, balance, request.Threshold, html.EscapeString(timestamp),
	)
	return subject, content
}

// SendUpstreamBalanceAlert sends one structured, root-authenticated monitor email.
// The recipient and SMTP transport are server configuration, never request input.
func SendUpstreamBalanceAlert(c *gin.Context) {
	var request upstreamBalanceAlertRequest
	if err := c.ShouldBindJSON(&request); err != nil || !validUpstreamBalanceAlertRequest(request) {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid balance alert"})
		return
	}
	recipient := strings.TrimSpace(os.Getenv("UPSTREAM_BALANCE_ALERT_EMAIL"))
	if err := common.Validate.Var(recipient, "required,email"); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"success": false,
			"message": "balance alert recipient is not configured",
		})
		return
	}
	subject, content := upstreamBalanceAlertContent(request)
	if err := sendUpstreamBalanceAlertEmail(subject, recipient, content); err != nil {
		common.SysLog("upstream balance alert email delivery failed")
		c.JSON(http.StatusBadGateway, gin.H{
			"success": false,
			"message": "balance alert email delivery failed",
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true})
}
