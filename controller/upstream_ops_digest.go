package controller

import (
	"fmt"
	"html"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

const upstreamOpsDigestMaxBodyBytes = 64 * 1024

var sendUpstreamOpsDigestEmail = common.SendEmail

type upstreamOpsDigestChannel struct {
	Name             string   `json:"name"`
	CollectionStatus string   `json:"collection_status"`
	AuditStatus      string   `json:"audit_status"`
	BalanceStatus    string   `json:"balance_status"`
	Balance          *float64 `json:"balance"`
	DailyCalls       int64    `json:"daily_calls"`
	DailyCostCNY     *float64 `json:"daily_cost_cny"`
	MonthCalls       int64    `json:"month_calls"`
	MonthCostCNY     float64  `json:"month_cost_cny"`
}

type upstreamOpsDigestPricing struct {
	Status         string         `json:"status"`
	Discovered     int64          `json:"discovered"`
	Applied        int64          `json:"applied"`
	Skipped        int64          `json:"skipped"`
	Blocked        int64          `json:"blocked"`
	ProtectedVideo int64          `json:"protected_video"`
	Reasons        map[string]int `json:"reasons"`
}

type upstreamOpsDigestAudit struct {
	OKChannels     int64 `json:"ok_channels"`
	FailedChannels int64 `json:"failed_channels"`
	Alerts         int64 `json:"alerts"`
}

type upstreamOpsDigestRequest struct {
	Date        string                     `json:"date"`
	GeneratedAt int64                      `json:"generated_at"`
	Channels    []upstreamOpsDigestChannel `json:"channels"`
	Pricing     upstreamOpsDigestPricing   `json:"pricing"`
	Audit       upstreamOpsDigestAudit     `json:"audit"`
}

func validDigestCode(value string) bool {
	if value == "" || len(value) > 80 {
		return false
	}
	for _, character := range value {
		if !((character >= 'a' && character <= 'z') ||
			(character >= '0' && character <= '9') ||
			character == '_' || character == '-' || character == '.') {
			return false
		}
	}
	return true
}

func validDigestCount(value int64) bool {
	return value >= 0 && value <= 1_000_000_000
}

func validDigestNumber(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && math.Abs(value) <= 1_000_000_000
}

func validUpstreamOpsDigest(request upstreamOpsDigestRequest) bool {
	date, err := time.Parse("2006-01-02", request.Date)
	if err != nil || date.Format("2006-01-02") != request.Date || request.GeneratedAt <= 0 ||
		len(request.Channels) == 0 || len(request.Channels) > 100 {
		return false
	}
	for _, channel := range request.Channels {
		name := strings.TrimSpace(channel.Name)
		if name == "" || utf8.RuneCountInString(name) > 80 ||
			strings.IndexFunc(name, unicode.IsControl) >= 0 ||
			!validDigestCode(channel.CollectionStatus) ||
			!validDigestCode(channel.AuditStatus) ||
			!validDigestCode(channel.BalanceStatus) ||
			!validDigestCount(channel.DailyCalls) || !validDigestCount(channel.MonthCalls) ||
			!validDigestNumber(channel.MonthCostCNY) {
			return false
		}
		if channel.Balance != nil && !validDigestNumber(*channel.Balance) {
			return false
		}
		if channel.DailyCostCNY != nil && !validDigestNumber(*channel.DailyCostCNY) {
			return false
		}
		if (channel.BalanceStatus == "complete") != (channel.Balance != nil) ||
			(channel.CollectionStatus == "complete") != (channel.DailyCostCNY != nil) {
			return false
		}
	}
	counts := []int64{
		request.Pricing.Discovered, request.Pricing.Applied, request.Pricing.Skipped,
		request.Pricing.Blocked, request.Pricing.ProtectedVideo,
		request.Audit.OKChannels, request.Audit.FailedChannels, request.Audit.Alerts,
	}
	if !validDigestCode(request.Pricing.Status) || len(request.Pricing.Reasons) > 30 {
		return false
	}
	for _, count := range counts {
		if !validDigestCount(count) {
			return false
		}
	}
	for reason, count := range request.Pricing.Reasons {
		if !validDigestCode(reason) || count < 0 || count > 1_000_000_000 {
			return false
		}
	}
	return true
}

func digestNumber(value *float64) string {
	if value == nil {
		return "未知"
	}
	return fmt.Sprintf("%.6f", *value)
}

func upstreamOpsDigestContent(request upstreamOpsDigestRequest) (string, string) {
	subject := fmt.Sprintf("[星途监控] 每日运营报告 %s", request.Date)
	var body strings.Builder
	body.WriteString("<h2>星途上游每日运营报告</h2>\n")
	body.WriteString("<p>业务日期（北京时间）：" + html.EscapeString(request.Date) + "</p>\n")
	body.WriteString("<table border=\"1\" cellpadding=\"5\" cellspacing=\"0\">\n<thead>\n<tr>\n")
	body.WriteString("<th>渠道</th><th>采集</th><th>审计</th><th>余额状态</th><th>余额</th><th>昨日调用</th><th>昨日成本(CNY)</th><th>本月调用</th><th>本月成本(CNY)</th>\n</tr>\n</thead>\n<tbody>\n")
	for _, channel := range request.Channels {
		body.WriteString("<tr><td>" + html.EscapeString(strings.TrimSpace(channel.Name)) + "</td>")
		body.WriteString("<td>" + html.EscapeString(channel.CollectionStatus) + "</td>")
		body.WriteString("<td>" + html.EscapeString(channel.AuditStatus) + "</td>")
		body.WriteString("<td>" + html.EscapeString(channel.BalanceStatus) + "</td>")
		body.WriteString("<td>" + digestNumber(channel.Balance) + "</td>")
		body.WriteString(fmt.Sprintf("<td>%d</td><td>%s</td><td>%d</td><td>%.6f</td></tr>\n",
			channel.DailyCalls, digestNumber(channel.DailyCostCNY), channel.MonthCalls, channel.MonthCostCNY))
	}
	body.WriteString("</tbody>\n</table>\n")
	body.WriteString(fmt.Sprintf(
		"<h3>自动改价</h3>\n<p>状态：%s；发现模型：%d；已改价：%d；保持原价：%d；安全阻断：%d；视频官方价保护：%d。</p>\n",
		html.EscapeString(request.Pricing.Status), request.Pricing.Discovered,
		request.Pricing.Applied, request.Pricing.Skipped, request.Pricing.Blocked,
		request.Pricing.ProtectedVideo,
	))
	reasons := make([]string, 0, len(request.Pricing.Reasons))
	for reason := range request.Pricing.Reasons {
		reasons = append(reasons, reason)
	}
	sort.Strings(reasons)
	if len(reasons) > 0 {
		body.WriteString("<p>改价原因：")
		for index, reason := range reasons {
			if index > 0 {
				body.WriteString("；")
			}
			body.WriteString(html.EscapeString(reason) + fmt.Sprintf("=%d", request.Pricing.Reasons[reason]))
		}
		body.WriteString("。</p>\n")
	}
	body.WriteString(fmt.Sprintf(
		"<h3>渠道审计</h3>\n<p>正常：%d；异常：%d；告警：%d。</p>\n",
		request.Audit.OKChannels, request.Audit.FailedChannels, request.Audit.Alerts,
	))
	body.WriteString("<p>本报告不包含账号、密码、Token、接口地址、提示词或生成结果。</p>\n")
	return subject, body.String()
}

// SendUpstreamOpsDigest delivers a bounded structured report to a fixed server-side recipient.
func SendUpstreamOpsDigest(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, upstreamOpsDigestMaxBodyBytes)
	var request upstreamOpsDigestRequest
	if err := c.ShouldBindJSON(&request); err != nil || !validUpstreamOpsDigest(request) {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid operations digest"})
		return
	}
	recipient := strings.TrimSpace(os.Getenv("UPSTREAM_BALANCE_ALERT_EMAIL"))
	if err := common.Validate.Var(recipient, "required,email"); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"success": false, "message": "digest recipient is not configured"})
		return
	}
	subject, content := upstreamOpsDigestContent(request)
	if err := sendUpstreamOpsDigestEmail(subject, recipient, content); err != nil {
		common.SysLog("upstream operations digest email delivery failed")
		c.JSON(http.StatusBadGateway, gin.H{"success": false, "message": "digest email delivery failed"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true})
}
