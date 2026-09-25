package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"sort"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"
)

var videoTitles = map[string]string{
	"wan3.0-video": "Wan 3.0", "wan3.0-video-prime": "Wan 3.0 Prime",
	"grok-imagine-1.5-video": "Grok Imagine 1.5 Video", "grok-video-3": "Grok Video 3",
	"grok-imagine-video-official": "Grok Imagine Video（官方）", "omni-flash": "Omni Flash", "flux-3-video": "FLUX 3 Video",
}

// marketJSON bounds catalog latency and preserves native session-based visibility.
func (s *server) marketJSON(ctx context.Context, url string, headers http.Header) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(ctx, "GET", url, nil)
	if e != nil {
		return nil, e
	}
	req.Header = headers
	r, e := s.client.Do(req)
	if e != nil {
		return nil, e
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("catalog_http_%d", r.StatusCode)
	}
	body, e := io.ReadAll(io.LimitReader(r.Body, 4*1024*1024+1))
	if e != nil {
		return nil, e
	}
	if len(body) > 4*1024*1024 {
		return nil, fmt.Errorf("catalog_too_large")
	}
	var out map[string]interface{}
	e = common.Unmarshal(body, &out)
	return out, e
}

// marketPricing is an additive read-only projection; it never changes billing tables.
func (s *server) marketPricing(c *gin.Context) {
	if c.Request.Method != "GET" && c.Request.Method != "HEAD" {
		response(c, 405, "method_not_allowed")
		return
	}
	headers := http.Header{}
	for _, key := range []string{"Cookie", "Authorization", "New-Api-User", "Accept-Language", "User-Agent"} {
		if value := c.GetHeader(key); value != "" {
			headers.Set(key, value)
		}
	}
	headers.Set("X-Forwarded-For", c.ClientIP())
	headers.Set("X-Forwarded-Proto", "https")
	// A downstream service credential is not a native console session.
	if headers.Get("Authorization") == "Bearer "+s.serviceToken {
		headers.Del("Authorization")
	}
	native, e := s.marketJSON(c.Request.Context(), s.native+"/api/pricing", headers)
	if e != nil {
		response(c, 503, "native_catalog_unavailable")
		return
	}
	rows, ok := native["data"].([]interface{})
	if !ok || native["success"] != true {
		response(c, 503, "native_catalog_invalid")
		return
	}
	native["video_catalog_status"] = "unavailable"
	if s.nativeReady() != nil {
		c.JSON(200, native)
		return
	}
	gatewayHeaders := http.Header{"Authorization": []string{"Bearer " + s.serviceToken}, "X-Xingtu-Contract-Version": []string{"xtai-video-billing-v2.2"}}
	caps, e := s.marketJSON(c.Request.Context(), s.backend+"/v1/capabilities", gatewayHeaders)
	if e != nil {
		c.JSON(200, native)
		return
	}
	prices, e := s.marketJSON(c.Request.Context(), s.backend+"/v1/video-prices", gatewayHeaders)
	if e != nil {
		c.JSON(200, native)
		return
	}
	capabilities, _ := caps["capabilities"].(map[string]interface{})
	video, _ := capabilities["video"].(map[string]interface{})
	models, _ := video["models"].([]interface{})
	if video["traffic_enabled"] != true {
		c.JSON(200, native)
		return
	}
	ratios, _ := native["group_ratio"].(map[string]interface{})
	groupRatio, ok := ratios["视频"].(float64)
	if !ok || groupRatio <= 0 {
		c.JSON(200, native)
		return
	}
	rate, e := decimal.NewFromString(s.rate)
	if e != nil || !rate.IsPositive() || common.QuotaPerUnit <= 0 {
		c.JSON(200, native)
		return
	}
	enabled := map[string]bool{}
	for _, value := range models {
		m, ok := value.(map[string]interface{})
		if ok && m["available"] == true {
			name, _ := m["id"].(string)
			enabled[name] = true
		}
	}
	pricing, _ := prices["pricing"].(map[string]interface{})
	priceRows, _ := pricing["models"].([]interface{})
	if models == nil || priceRows == nil || caps["billing_contract_version"] != "xtai-video-billing-v2.2" || pricing["contract_version"] != "xtai-video-pricing-v1" {
		native["video_catalog_reason"] = "gateway_schema_mismatch"
		c.JSON(200, native)
		return
	}
	seen := map[string]bool{}
	for _, value := range rows {
		m, ok := value.(map[string]interface{})
		if ok {
			name, _ := m["model_name"].(string)
			seen[name] = true
		}
	}
	names := make([]string, 0, len(specs))
	for name := range specs {
		names = append(names, name)
	}
	sort.Strings(names)
	added := []interface{}{}
	missing := []string{}
	for _, name := range names {
		spec := specs[name]
		if seen[name] {
			continue
		}
		if !enabled[name] {
			missing = append(missing, name)
			continue
		}
		reserve, e := decimal.NewFromString(spec.Reserve)
		if e != nil {
			continue
		}
		verified := false
		revision := ""
		for _, value := range priceRows {
			p, ok := value.(map[string]interface{})
			if !ok || p["model"] != name || p["resolution"] != spec.Resolution || p["currency"] != "CNY" || p["billing_unit"] != "output_second" {
				continue
			}
			raw, _ := p["cny_per_second_exact"].(string)
			unit, e := decimal.NewFromString(raw)
			if e == nil && unit.Mul(decimal.NewFromInt(int64(spec.Duration))).Equal(reserve) {
				verified = true
				revision, _ = p["pricing_revision"].(string)
				break
			}
		}
		if !verified {
			missing = append(missing, name)
			continue
		}
		// Native UI multiplies model_price by group_ratio. Normalize only its display,
		// while retaining the exact CNY reservation and specification for API consumers.
		displayPrice, _ := reserve.Mul(rate).Div(decimal.NewFromFloat(common.QuotaPerUnit)).Div(decimal.NewFromFloat(groupRatio)).Float64()
		added = append(added, gin.H{"model_name": name, "display_name": videoTitles[name], "description": fmt.Sprintf("%s · 文生视频 %s / %d秒 / 16:9 / 保留音轨；该规格预扣¥%s，按实际成本×1.5结算。JSON POST /v1/videos。", videoTitles[name], spec.Resolution, spec.Duration, spec.Reserve), "quota_type": 1, "model_ratio": 0, "model_price": displayPrice, "owner_by": "NodyHub", "completion_ratio": 0, "enable_groups": []string{"视频"}, "supported_endpoint_types": []string{"video-generation"}, "public_video_pricing": gin.H{"currency": "CNY", "reserved_cny_exact": spec.Reserve, "duration": spec.Duration, "resolution": spec.Resolution, "aspect_ratio": "16:9", "generate_audio": true, "billing_mode": "actual_cost_times_1_5", "pricing_revision": revision}})
	}
	endpoints, _ := native["supported_endpoint"].(map[string]interface{})
	if endpoints == nil {
		endpoints = map[string]interface{}{}
	}
	endpoints["video-generation"] = gin.H{"path": "/v1/videos", "method": "POST"}
	native["supported_endpoint"] = endpoints
	material, _ := common.Marshal(gin.H{"native_version": native["pricing_version"], "video_rows": added})
	sum := sha256.Sum256(material)
	native["pricing_version"] = fmt.Sprintf("%x", sum[:])
	native["data"] = append(rows, added...)
	native["video_catalog_status"] = "ready"
	if len(missing) > 0 {
		native["video_catalog_status"] = "partial"
	}
	native["video_catalog_missing"] = missing
	native["video_catalog_added"] = len(added)
	c.JSON(200, native)
}
