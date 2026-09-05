package controller

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestSendUpstreamOpsDigestUsesFixedRecipientAndEscapesNames(t *testing.T) {
	gin.SetMode(gin.TestMode)
	previousRecipient := os.Getenv("UPSTREAM_BALANCE_ALERT_EMAIL")
	t.Cleanup(func() { _ = os.Setenv("UPSTREAM_BALANCE_ALERT_EMAIL", previousRecipient) })
	_ = os.Setenv("UPSTREAM_BALANCE_ALERT_EMAIL", "fixed@example.com")
	previousSender := sendUpstreamOpsDigestEmail
	t.Cleanup(func() { sendUpstreamOpsDigestEmail = previousSender })

	sendUpstreamOpsDigestEmail = func(subject, recipient, content string) error {
		if recipient != "fixed@example.com" {
			t.Fatalf("unexpected recipient %q", recipient)
		}
		if !strings.Contains(subject, "2026-08-11") {
			t.Fatalf("subject lacks business date: %q", subject)
		}
		if !strings.Contains(subject, "每日运营报告") || !strings.Contains(content, "渠道审计") {
			t.Fatalf("digest contains unreadable headings: subject=%q content=%q", subject, content)
		}
		if strings.Contains(content, "<script>") || !strings.Contains(content, "&lt;script&gt;") {
			t.Fatalf("channel name was not escaped: %s", content)
		}
		return nil
	}

	router := gin.New()
	router.POST("/digest", SendUpstreamOpsDigest)
	body := `{"date":"2026-08-11","generated_at":1786532522,"channels":[{"name":"<script>","collection_status":"complete","audit_status":"ok","balance_status":"complete","balance":1.2,"daily_calls":5,"daily_cost_cny":3.5,"month_calls":7,"month_cost_cny":4.75}],"pricing":{"status":"complete","discovered":3,"applied":1,"skipped":2,"blocked":1,"protected_video":1,"reasons":{"upstream_collection_incomplete":1}},"audit":{"ok_channels":1,"failed_channels":0,"alerts":1}}`
	request := httptest.NewRequest(http.MethodPost, "/digest", bytes.NewBufferString(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
}

func TestSendUpstreamOpsDigestRejectsInvalidAndOversizedPayloads(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/digest", SendUpstreamOpsDigest)

	for name, body := range map[string]string{
		"control name":   `{"date":"2026-08-11","generated_at":1,"channels":[{"name":"bad\nname"}]}`,
		"negative calls": `{"date":"2026-08-11","generated_at":1,"channels":[{"name":"one","daily_calls":-1}]}`,
		"oversized":      `{"date":"2026-08-11","generated_at":1,"channels":[{"name":"` + strings.Repeat("x", 70*1024) + `"}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/digest", bytes.NewBufferString(body))
			request.Header.Set("Content-Type", "application/json")
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			if response.Code != http.StatusBadRequest {
				t.Fatalf("expected 400, got %d", response.Code)
			}
		})
	}
}

func TestUpstreamOpsDigestContentRespectsSMTPLineLimit(t *testing.T) {
	channels := make([]upstreamOpsDigestChannel, 100)
	for index := range channels {
		balance := 1.25
		dailyCost := 0.5
		channels[index] = upstreamOpsDigestChannel{
			Name:             strings.Repeat("渠", 80),
			CollectionStatus: "complete",
			AuditStatus:      "ok",
			BalanceStatus:    "complete",
			Balance:          &balance,
			DailyCalls:       10,
			DailyCostCNY:     &dailyCost,
			MonthCalls:       100,
			MonthCostCNY:     5,
		}
	}
	reasons := make(map[string]int, 30)
	for index := 0; index < 30; index++ {
		reasons[fmt.Sprintf("reason_%02d_%s", index, strings.Repeat("x", 68))] = index
	}
	_, content := upstreamOpsDigestContent(upstreamOpsDigestRequest{
		Date:        "2026-09-05",
		GeneratedAt: 1,
		Channels:    channels,
		Pricing: upstreamOpsDigestPricing{
			Status:  "complete",
			Reasons: reasons,
		},
	})

	for lineNumber, line := range strings.Split(content, "\r\n") {
		if len([]byte(line)) > 998 {
			t.Fatalf("SMTP line %d is %d bytes; maximum is 998", lineNumber+1, len([]byte(line)))
		}
	}
}
