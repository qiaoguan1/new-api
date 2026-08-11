package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
)

const (
	xingTuWebhookURLKey    = "XINGTU_VIDEO_WEBHOOK_URL"
	xingTuWebhookSecretKey = "XINGTU_VIDEO_WEBHOOK_SECRET"
	XingTuWebhookUserAgent = "XingTuVideoWebhook/1"
)

type xingTuWebhookConfig struct {
	URL    *url.URL
	Secret string
}

var blockedXingTuWebhookPrefixes = []netip.Prefix{
	netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("64:ff9b::/96"),
	netip.MustParsePrefix("2001:2::/48"),
	netip.MustParsePrefix("2001:10::/28"),
	netip.MustParsePrefix("2001:db8::/32"),
}

func SignXingTuVideoWebhook(secret, timestamp string, body []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(timestamp + "."))
	_, _ = mac.Write(body)
	return "v1=" + hex.EncodeToString(mac.Sum(nil))
}

func validateXingTuWebhookConfig(rawURL, secret string) (*xingTuWebhookConfig, error) {
	if rawURL == "" && secret == "" {
		return nil, nil
	}
	if rawURL == "" || secret == "" {
		return nil, errors.New("both callback URL and secret are required")
	}
	if len([]byte(secret)) < 32 {
		return nil, errors.New("callback secret must contain at least 32 bytes")
	}
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" {
		return nil, errors.New("callback URL must be an absolute HTTPS URL")
	}
	if u.User != nil || u.Fragment != "" || u.RawQuery != "" {
		return nil, errors.New("callback URL cannot contain userinfo, query, or fragment")
	}
	if net.ParseIP(u.Hostname()) != nil {
		return nil, errors.New("callback URL must use a public DNS hostname")
	}
	if port := u.Port(); port != "" && port != "443" {
		return nil, errors.New("callback URL may only use port 443")
	}
	return &xingTuWebhookConfig{URL: u, Secret: secret}, nil
}

func newXingTuWebhookHTTPClient(transport http.RoundTripper) *http.Client {
	return &http.Client{
		Transport: transport,
		Timeout:   10 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func publicCallbackIP(ip net.IP) bool {
	addr, ok := netip.AddrFromSlice(ip)
	if !ok {
		return false
	}
	addr = addr.Unmap()
	if !addr.IsGlobalUnicast() || addr.IsPrivate() || addr.IsLoopback() || addr.IsLinkLocalUnicast() {
		return false
	}
	for _, prefix := range blockedXingTuWebhookPrefixes {
		if prefix.Contains(addr) {
			return false
		}
	}
	return true
}

func secureXingTuWebhookTransport(config *xingTuWebhookConfig) *http.Transport {
	hostname := config.URL.Hostname()
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	return &http.Transport{
		Proxy:                 nil,
		TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS12, ServerName: hostname},
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: 5 * time.Second,
		DisableCompression:    true,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(address)
			if err != nil || !strings.EqualFold(strings.TrimSuffix(host, "."), strings.TrimSuffix(hostname, ".")) || port != "443" {
				return nil, errors.New("callback dial target does not match configured host")
			}
			ips, err := net.DefaultResolver.LookupIP(ctx, "ip", hostname)
			if err != nil {
				return nil, err
			}
			publicIPs := make([]net.IP, 0, len(ips))
			for _, wantIPv6 := range []bool{true, false} {
				for _, ip := range ips {
					if publicCallbackIP(ip) && (ip.To4() == nil) == wantIPv6 {
						publicIPs = append(publicIPs, ip)
					}
				}
			}
			var lastErr error
			for _, ip := range publicIPs {
				conn, dialErr := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
				if dialErr == nil {
					return conn, nil
				}
				lastErr = dialErr
			}
			if lastErr != nil {
				return nil, lastErr
			}
			return nil, errors.New("callback hostname did not resolve to a public IP")
		},
	}
}

func deliverXingTuVideoWebhook(ctx context.Context, client *http.Client, config *xingTuWebhookConfig, event *model.XingTuVideoWebhookEvent) (int, string, error) {
	timestamp := strconv.FormatInt(time.Now().Unix(), 10)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, config.URL.String(), bytes.NewReader(event.Payload))
	if err != nil {
		return 0, "invalid_event", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", XingTuWebhookUserAgent)
	req.Header.Set("X-XingTu-Contract-Version", event.ContractVersion)
	req.Header.Set("X-XingTu-Event-ID", event.EventID)
	req.Header.Set("X-XingTu-Timestamp", timestamp)
	req.Header.Set("X-XingTu-Delivery-Attempt", strconv.Itoa(event.Attempts))
	req.Header.Set("X-XingTu-Signature", SignXingTuVideoWebhook(config.Secret, timestamp, event.Payload))
	resp, err := client.Do(req)
	if err != nil {
		return 0, "network_error", err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return resp.StatusCode, "non_2xx", fmt.Errorf("callback returned HTTP %d", resp.StatusCode)
	}
	return resp.StatusCode, "", nil
}

// RunXingTuVideoWebhookOnce claims and delivers at most one durable event.
func RunXingTuVideoWebhookOnce(ctx context.Context, config *xingTuWebhookConfig, client *http.Client) (bool, error) {
	event, err := model.ClaimDueXingTuVideoWebhook(time.Now(), time.Minute)
	if err != nil || event == nil {
		return false, err
	}
	status, code, deliveryErr := deliverXingTuVideoWebhook(ctx, client, config, event)
	completeErr := model.CompleteXingTuVideoWebhook(event.ID, event.Attempts, deliveryErr == nil, status, code, time.Now())
	if completeErr != nil {
		return true, completeErr
	}
	return true, deliveryErr
}

// StartXingTuVideoWebhookWorker starts only when both operator-owned settings
// validate. Missing settings intentionally leave durable events queued.
func StartXingTuVideoWebhookWorker() {
	config, err := validateXingTuWebhookConfig(os.Getenv(xingTuWebhookURLKey), os.Getenv(xingTuWebhookSecretKey))
	if err != nil {
		common.SysError("XingTu video webhook disabled: invalid configuration")
		return
	}
	if config == nil {
		common.SysLog("XingTu video webhook delivery disabled: callback URL is not configured")
		return
	}
	client := newXingTuWebhookHTTPClient(secureXingTuWebhookTransport(config))
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			for i := 0; i < 20; i++ {
				processed, runErr := RunXingTuVideoWebhookOnce(context.Background(), config, client)
				if runErr != nil {
					common.SysError("XingTu video webhook delivery failed")
				}
				if !processed {
					break
				}
			}
		}
	}()
}
