package controller

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestSendUpstreamBalanceAlertUsesFixedRecipient(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("UPSTREAM_BALANCE_ALERT_EMAIL", "961246161@qq.com")
	original := sendUpstreamBalanceAlertEmail
	t.Cleanup(func() { sendUpstreamBalanceAlertEmail = original })
	var recipient, subject, content string
	sendUpstreamBalanceAlertEmail = func(gotSubject, gotRecipient, gotContent string) error {
		subject, recipient, content = gotSubject, gotRecipient, gotContent
		return nil
	}

	router := gin.New()
	router.POST("/alert", SendUpstreamBalanceAlert)
	body := `{"kind":"balance_depleted","name":"Paisio","balance":0,"threshold":0,"occurred_at":1786407600,"recipient":"attacker@example.com"}`
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/alert", bytes.NewBufferString(body))
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if recipient != "961246161@qq.com" {
		t.Fatalf("unexpected recipient %q", recipient)
	}
	if !strings.Contains(subject, "上游账户余额耗尽") || !strings.Contains(subject, "Paisio") ||
		!strings.Contains(content, "0.000000") || !strings.Contains(content, "北京时间") {
		t.Fatalf("missing sanitized event data: subject=%q content=%q", subject, content)
	}
}

func TestSendUpstreamBalanceAlertRejectsArbitraryKind(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("UPSTREAM_BALANCE_ALERT_EMAIL", "961246161@qq.com")
	router := gin.New()
	router.POST("/alert", SendUpstreamBalanceAlert)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/alert",
		bytes.NewBufferString(`{"kind":"arbitrary_html","name":"<b>x</b>","occurred_at":1786407600}`),
	)
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestSendUpstreamBalanceAlertRejectsOversizedPayload(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/alert", SendUpstreamBalanceAlert)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/alert",
		bytes.NewBufferString(`{"kind":"test","name":"`+strings.Repeat("x", 9*1024)+`","occurred_at":1786407600}`),
	)
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", recorder.Code)
	}
}

func TestValidUpstreamBalanceAlertRejectsUnboundedNumbers(t *testing.T) {
	tooLarge := 1_000_000_001.0
	if validUpstreamBalanceAlertRequest(upstreamBalanceAlertRequest{
		Kind: "balance_depleted", Name: "Paisio", Balance: &tooLarge, OccurredAt: 1,
	}) {
		t.Fatal("expected an unbounded balance to be rejected")
	}
	if validUpstreamBalanceAlertRequest(upstreamBalanceAlertRequest{
		Kind: "test", Name: "Monitor", Threshold: tooLarge, OccurredAt: 1,
	}) {
		t.Fatal("expected an unbounded threshold to be rejected")
	}
}

func TestSendUpstreamBalanceAlertRequiresConfiguredValidRecipient(t *testing.T) {
	gin.SetMode(gin.TestMode)
	previous, existed := os.LookupEnv("UPSTREAM_BALANCE_ALERT_EMAIL")
	os.Unsetenv("UPSTREAM_BALANCE_ALERT_EMAIL")
	t.Cleanup(func() {
		if existed {
			os.Setenv("UPSTREAM_BALANCE_ALERT_EMAIL", previous)
		}
	})
	router := gin.New()
	router.POST("/alert", SendUpstreamBalanceAlert)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/alert",
		bytes.NewBufferString(`{"kind":"test","name":"Monitor","occurred_at":1786407600}`),
	)
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d: %s", recorder.Code, recorder.Body.String())
	}
}
