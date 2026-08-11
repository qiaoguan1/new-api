package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSignXingTuVideoWebhook(t *testing.T) {
	body := []byte(`{"event_id":"evt_123"}`)
	signature := SignXingTuVideoWebhook("01234567890123456789012345678901", "1786406400", body)
	mac := hmac.New(sha256.New, []byte("01234567890123456789012345678901"))
	_, _ = mac.Write([]byte("1786406400."))
	_, _ = mac.Write(body)
	assert.Equal(t, "v1="+hex.EncodeToString(mac.Sum(nil)), signature)
}

func TestDeliverXingTuVideoWebhookSendsSignedContract(t *testing.T) {
	secret := "01234567890123456789012345678901"
	var received bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received = true
		body, err := io.ReadAll(r.Body)
		require.NoError(t, err)
		timestamp := r.Header.Get("X-XingTu-Timestamp")
		assert.Equal(t, SignXingTuVideoWebhook(secret, timestamp, body), r.Header.Get("X-XingTu-Signature"))
		assert.Equal(t, "evt_delivery", r.Header.Get("X-XingTu-Event-ID"))
		assert.Equal(t, "2", r.Header.Get("X-XingTu-Delivery-Attempt"))
		assert.Equal(t, "xtai-video-billing-v2", r.Header.Get("X-XingTu-Contract-Version"))
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	u, err := url.Parse(server.URL)
	require.NoError(t, err)
	event := &model.XingTuVideoWebhookEvent{
		EventID: "evt_delivery", ContractVersion: "xtai-video-billing-v2", Attempts: 2,
		Payload: []byte(`{"event_id":"evt_delivery"}`),
	}
	status, code, err := deliverXingTuVideoWebhook(context.Background(), server.Client(), &xingTuWebhookConfig{URL: u, Secret: secret}, event)
	require.NoError(t, err)
	assert.Equal(t, http.StatusNoContent, status)
	assert.Empty(t, code)
	assert.True(t, received)
}

func TestValidateXingTuWebhookConfigFailsClosed(t *testing.T) {
	_, err := validateXingTuWebhookConfig("http://callback.example.com/webhook", "01234567890123456789012345678901")
	require.Error(t, err)
	_, err = validateXingTuWebhookConfig("https://127.0.0.1/webhook", "01234567890123456789012345678901")
	require.Error(t, err)
	_, err = validateXingTuWebhookConfig("https://callback.example.com/webhook", "short")
	require.Error(t, err)
}

func TestPublicCallbackIPRejectsSpecialNetworks(t *testing.T) {
	assert.True(t, publicCallbackIP(net.ParseIP("8.8.8.8")))
	assert.True(t, publicCallbackIP(net.ParseIP("2606:4700:4700::1111")))
	for _, raw := range []string{"127.0.0.1", "10.0.0.1", "100.64.0.1", "192.0.2.1", "198.18.0.1", "::1", "fc00::1", "2001:db8::1"} {
		assert.False(t, publicCallbackIP(net.ParseIP(raw)), raw)
	}
}

func TestWebhookClientDoesNotFollowRedirects(t *testing.T) {
	targetCalled := false
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		targetCalled = true
		w.WriteHeader(http.StatusNoContent)
	}))
	defer target.Close()
	redirect := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()

	client := newXingTuWebhookHTTPClient(redirect.Client().Transport)
	resp, err := client.Get(redirect.URL)
	require.NoError(t, err)
	defer resp.Body.Close()
	assert.Equal(t, http.StatusTemporaryRedirect, resp.StatusCode)
	assert.False(t, targetCalled)
}
