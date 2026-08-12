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
	"patrol_incident_open":         "中转站巡检发现异常",
	"patrol_incident_reminder":     "中转站巡检异常仍未恢复",
	"patrol_incident_recovered":    "中转站巡检异常已恢复",
	"balance_depleted":             "上游账户余额耗尽",
	"balance_depleted_reminder":    "上游账户余额仍未恢复",
	"balance_recovered":            "上游账户余额已恢复",
	"balance_collection_failed":    "上游余额监控连续采集失败",
	"balance_collection_recovered": "上游余额监控已恢复",
	"credential_expiring":          "视频上游账单授权即将到期",
	"credential_expired":           "视频上游账单授权已到期",
	"credential_refresh_failed":    "视频上游账单授权刷新失败",
	"credential_refresh_recovered": "视频上游账单授权刷新已恢复",
	"test":                         "上游余额监控测试邮件",
}

type upstreamBalanceAlertRequest struct {
	Kind       string   `json:"kind"`
	Name       string   `json:"name"`
	Balance    *float64 `json:"balance"`
	Threshold  float64  `json:"threshold"`
	OccurredAt int64    `json:"occurred_at"`
	Code       string   `json:"code"`
	Severity   string   `json:"severity"`
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
	if strings.HasPrefix(request.Kind, "patrol_") {
		if utf8.RuneCountInString(request.Code) < 1 || utf8.RuneCountInString(request.Code) > 80 ||
			strings.IndexFunc(request.Code, unicode.IsControl) >= 0 {
			return false
		}
		if request.Severity != "info" && request.Severity != "warning" && request.Severity != "critical" {
			return false
		}
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
	if strings.HasPrefix(request.Kind, "patrol_") {
		content := fmt.Sprintf(
			"<p>%s</p><p>检查项：%s<br>严重级别：%s<br>故障代码：%s<br>北京时间：%s</p><p>自动修复仅限白名单内的可逆操作；定价、余额、结算证据和凭据异常不会被猜测写入。请登录服务器查看私有巡检报告。</p>",
			html.EscapeString(title), name, html.EscapeString(request.Severity),
			html.EscapeString(request.Code), html.EscapeString(timestamp),
		)
		return subject, content
	}
	if strings.HasPrefix(request.Kind, "credential_") {
		content := fmt.Sprintf(
			"<p>%s</p><p>视频上游：%s<br>告警阈值（天）：%.0f<br>北京时间：%s</p><p>账单授权只影响新任务资格；历史任务查询、结算重试和 Webhook 继续运行。本邮件不包含账号、密码、Token 或接口地址。</p>",
			html.EscapeString(title), name, request.Threshold, html.EscapeString(timestamp),
		)
		return subject, content
	}
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
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 8*1024)
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
