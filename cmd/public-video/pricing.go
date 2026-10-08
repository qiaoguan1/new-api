package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"strings"
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
	imageCapabilities := map[string]map[string]interface{}{}
	mediaCapabilities := map[string]map[string]interface{}{}
	referenceMetadata := map[string]map[string]interface{}{}
	operatorCapabilities := map[string]map[string]interface{}{}
	for _, value := range models {
		m, ok := value.(map[string]interface{})
		if ok && m["available"] == true {
			name, _ := m["id"].(string)
			enabled[name] = true
			images, _ := m["image_reference"].(map[string]interface{})
			if images["available"] == true {
				imageCapabilities[name] = images
			}
			media, _ := m["media_reference"].(map[string]interface{})
			if media["available"] == true {
				mediaCapabilities[name] = media
				referenceMetadata[name] = m
			}
			operator, _ := m["operator_testing"].(map[string]interface{})
			if operator["supported"] == true && operator["available"] == true && operator["verification_status"] == "unverified" && operator["admission_mode"] == "operator_testing" {
				operatorCapabilities[name] = operator
				referenceMetadata[name] = m
			}
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
		if images := imageCapabilities[name]; images != nil {
			imagePrices, _ := prices["image_reference_pricing"].(map[string]interface{})
			imagePriceRows, _ := imagePrices["models"].([]interface{})
			imageSpecs, _ := images["specifications"].([]interface{})
			verifiedSpecs := []interface{}{}
			for _, candidate := range imageSpecs {
				row, ok := candidate.(map[string]interface{})
				if !ok || row["model"] != name || row["currency"] != "CNY" {
					continue
				}
				mode, modeOK := row["operation_mode"].(string)
				count, countOK := row["image_count"].(float64)
				duration, durationOK := row["duration"].(float64)
				resolution, resolutionOK := row["resolution"].(string)
				amount, amountOK := row["amount_cny_exact"].(string)
				revision, revisionOK := row["pricing_revision"].(string)
				priceAmount, amountErr := decimal.NewFromString(amount)
				if !modeOK || (mode != "reference" && mode != "all_reference") || !countOK || count != math.Trunc(count) || count < 1 || count > 7 || (mode == "reference" && count != 1) || (mode == "all_reference" && count < 2) || !durationOK || duration != math.Trunc(duration) || duration < 1 || duration > 30 || !resolutionOK || (resolution != "480p" && resolution != "720p") || !amountOK || amountErr != nil || !priceAmount.IsPositive() || !revisionOK || revision == "" {
					continue
				}
				for _, priceValue := range imagePriceRows {
					price, ok := priceValue.(map[string]interface{})
					if !ok || price["model"] != name || price["currency"] != "CNY" {
						continue
					}
					match := true
					for _, field := range []string{"operation_mode", "image_count", "resolution", "duration", "amount_cny_exact", "pricing_revision"} {
						if other, ok := price[field].(string); ok {
							if row[field] != other {
								match = false
							}
						} else if other, ok := price[field].(float64); ok {
							if row[field] != other {
								match = false
							}
						} else {
							match = false
						}
					}
					if match {
						verifiedSpecs = append(verifiedSpecs, row)
						break
					}
				}
			}
			if len(verifiedSpecs) > 0 {
				projection := added[len(added)-1].(gin.H)
				projection["image_reference"] = gin.H{"available": true, "identity_required": true, "roles": []string{"reference"}, "required_input_aspect_ratio": "16:9", "specifications": verifiedSpecs}
				projection["description"] = projection["description"].(string) + "另支持已验证图片参考；模式、图片数量、分辨率和时长须按image_reference.specifications选择，不支持未验证首尾帧。"
			}
		}
		if media := mediaCapabilities[name]; media != nil {
			mediaPricing, _ := prices["media_reference_pricing"].(map[string]interface{})
			if mediaPricing["contract_version"] == "xtai-video-billing-v2.2" && mediaPricing["currency"] == "CNY" {
				candidates, _ := media["specifications"].([]interface{})
				priceCandidates, _ := mediaPricing["models"].([]interface{})
				verifiedSpecs := verifiedMediaSpecifications(name, candidates, priceCandidates)
				if len(verifiedSpecs) > 0 {
					projection := added[len(added)-1].(gin.H)
					projection["media_reference"] = gin.H{"available": true, "identity_required": true, "specifications": verifiedSpecs}
					for _, kind := range []string{"reference_video", "reference_audio", "reference_video_audio"} {
						metadata, _ := referenceMetadata[name][kind].(map[string]interface{})
						if public := verifiedReferenceMetadata(kind, metadata, verifiedSpecs); public != nil {
							projection[kind] = public
						}
					}
					projection["description"] = projection["description"].(string) + "另支持已验证媒体模式；模式、素材数量、音轨、比例及精确输入秒数须按media_reference.specifications选择。"
				}
			}
		}
		if operator := operatorCapabilities[name]; operator != nil {
			operatorPricing, _ := prices["operator_testing"].(map[string]interface{})
			if operatorPricing["enabled"] == true && operatorPricing["verification_status"] == "unverified" && operatorPricing["admission_mode"] == "operator_testing" {
				candidates, _ := operator["rules"].([]interface{})
				priceCandidates, _ := operatorPricing["rules"].([]interface{})
				rules := verifiedOperatorRules(name, candidates, priceCandidates)
				if len(rules) > 0 {
					projection := added[len(added)-1].(gin.H)
					projection["operator_testing"] = gin.H{"supported": true, "available": true, "enabled": true, "pricing_kind": "estimated_reservation", "verification_status": "unverified", "admission_mode": "operator_testing", "is_upper_bound": false, "warning": operatorEstimateWarning, "rules": rules}
					for _, kind := range []string{"reference_video", "reference_audio", "reference_video_audio"} {
						metadata, _ := referenceMetadata[name][kind].(map[string]interface{})
						if public := operatorReferenceMetadata(kind, metadata, rules); public != nil {
							if prior, ok := projection[kind].(map[string]interface{}); ok {
								public["specifications"] = prior["specifications"]
							}
							projection[kind] = public
						}
					}
					projection["description"] = projection["description"].(string) + "另开放未验收手测候选模式；预扣仅为估算、不是费用上限，最终按实际上游账单×1.5结算。"
				}
			}
		}
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

// verifiedMediaSpecifications joins exact public capability and retail tuples.
// It copies only public discriminators, so a private cost or evidence field in a
// gateway catalog cannot be exposed through the native marketplace projection.
func verifiedMediaSpecifications(name string, candidates, prices []interface{}) []interface{} {
	result := []interface{}{}
	limits, knownModel := nodyMediaCandidateLimits[name]
	if !knownModel || len(candidates) > 80 || len(prices) > 80 {
		return result
	}
	for _, candidate := range candidates {
		row, ok := candidate.(map[string]interface{})
		if !ok || row["model"] != name || row["currency"] != "CNY" {
			continue
		}
		mode, modeOK := row["operation_mode"].(string)
		resolution, resolutionOK := row["resolution"].(string)
		duration, durationOK := row["duration"].(float64)
		aspect, aspectOK := row["aspect_ratio"].(string)
		_, audioOK := row["generate_audio"].(bool)
		imageCount, imageOK := row["image_count"].(float64)
		videoCount, videoOK := row["video_count"].(float64)
		audioCount, audioCountOK := row["audio_count"].(float64)
		amount, amountOK := row["amount_cny_exact"].(string)
		revision, revisionOK := row["pricing_revision"].(string)
		if !modeOK || (mode != "reference" && mode != "all_reference" && mode != "first_frame" && mode != "last_frame" && mode != "first_last_frame") {
			continue
		}
		if !resolutionOK || (resolution != "480p" && resolution != "720p" && resolution != "1080p" && resolution != "4k") || !durationOK || duration != math.Trunc(duration) || duration < 1 || duration > 30 || !aspectOK || !canonicalMediaAspectRatio(aspect) || !audioOK {
			continue
		}
		if !imageOK || !videoOK || !audioCountOK || imageCount != math.Trunc(imageCount) || videoCount != math.Trunc(videoCount) || audioCount != math.Trunc(audioCount) || imageCount < 0 || videoCount < 0 || audioCount < 0 || imageCount > float64(limits.images) || videoCount > float64(limits.videos) || audioCount > float64(limits.audios) || imageCount+videoCount+audioCount == 0 {
			continue
		}
		if !amountOK || len(amount) > 10 || !mediaSecondsPattern.MatchString(amount) || !revisionOK || strings.TrimSpace(revision) == "" || len(revision) > 120 {
			continue
		}
		price, amountErr := decimal.NewFromString(amount)
		if amountErr != nil || !price.IsPositive() || price.GreaterThan(decimal.NewFromInt(150)) {
			continue
		}
		if name == "omni-flash" && imageCount == 2 {
			continue
		}
		if (mode == "first_frame" || mode == "last_frame") && (imageCount != 1 || videoCount+audioCount != 0) {
			continue
		}
		if mode == "first_last_frame" && (imageCount != 2 || videoCount+audioCount != 0) {
			continue
		}
		public := map[string]interface{}{}
		for _, field := range []string{"model", "currency", "operation_mode", "resolution", "duration", "aspect_ratio", "generate_audio", "image_count", "video_count", "audio_count", "amount_cny_exact", "pricing_revision"} {
			public[field] = row[field]
		}
		validSeconds := true
		for field, count := range map[string]float64{"input_video_seconds_exact": videoCount, "input_audio_seconds_exact": audioCount} {
			value, exists := row[field]
			if count == 0 {
				if exists {
					validSeconds = false
				}
				continue
			}
			seconds, ok := value.(string)
			if !ok || len(seconds) > 9 || !mediaSecondsPattern.MatchString(seconds) {
				validSeconds = false
				break
			}
			parsed, err := decimal.NewFromString(seconds)
			if err != nil || parsed.LessThan(decimal.NewFromFloat(count)) || parsed.GreaterThan(decimal.NewFromFloat(count*15)) {
				validSeconds = false
				break
			}
			public[field] = seconds
		}
		if !validSeconds {
			continue
		}
		for _, priceCandidate := range prices {
			other, ok := priceCandidate.(map[string]interface{})
			if !ok {
				continue
			}
			match := true
			for field, value := range public {
				if other[field] != value {
					match = false
					break
				}
			}
			for _, field := range []string{"input_video_seconds_exact", "input_audio_seconds_exact"} {
				if public[field] == nil && other[field] != nil {
					match = false
				}
			}
			if match {
				result = append(result, public)
				break
			}
		}
	}
	return result
}

// verifiedReferenceMetadata preserves existing reference capability fields, but
// derives availability, counts and combination flags solely from retail-matched
// profiles. Only bounded known metadata fields cross the public boundary.
func verifiedReferenceMetadata(kind string, metadata map[string]interface{}, specifications []interface{}) map[string]interface{} {
	matched := []interface{}{}
	resolutions := []string{}
	seen := map[string]bool{}
	maxVideos, maxAudios, maxAssets := float64(0), float64(0), float64(0)
	withImages, withVideos, withAudios, withGeneratedAudio, audioOnly := false, false, false, false, false
	for _, specification := range specifications {
		row := specification.(map[string]interface{})
		images, videos, audios := row["image_count"].(float64), row["video_count"].(float64), row["audio_count"].(float64)
		if (kind == "reference_video" && videos == 0) || (kind == "reference_audio" && audios == 0) || (kind == "reference_video_audio" && (videos == 0 || audios == 0)) {
			continue
		}
		matched = append(matched, row)
		resolution := row["resolution"].(string)
		if !seen[resolution] {
			resolutions = append(resolutions, resolution)
			seen[resolution] = true
		}
		maxVideos, maxAudios = math.Max(maxVideos, videos), math.Max(maxAudios, audios)
		maxAssets = math.Max(maxAssets, images+videos+audios)
		withImages, withVideos, withAudios = withImages || images > 0, withVideos || videos > 0, withAudios || audios > 0
		withGeneratedAudio = withGeneratedAudio || row["generate_audio"] == true
		audioOnly = audioOnly || (audios > 0 && images+videos == 0)
	}
	if len(matched) == 0 {
		return nil
	}
	maxCount := maxVideos
	if kind == "reference_audio" {
		maxCount = maxAudios
	}
	public := map[string]interface{}{"supported": true, "available": true, "identity_required": true, "available_resolutions": resolutions, "max_count": maxCount, "max_total_assets": maxAssets, "specifications": matched, "reason": ""}
	formats := map[string]map[string]bool{
		"roles":        {"reference_video": true, "reference_audio": true},
		"mime_types":   {"video/mp4": true, "audio/mpeg": true, "audio/wav": true, "audio/x-wav": true, "audio/aac": true, "audio/mp4": true, "audio/x-m4a": true},
		"video_codecs": {"h264": true, "hevc": true},
		"audio_codecs": {"mp3": true, "wav": true, "aac": true, "m4a": true},
	}
	for field, allowed := range formats {
		values, ok := metadata[field].([]interface{})
		if !ok || len(values) == 0 || len(values) > len(allowed) {
			continue
		}
		valid := true
		for _, value := range values {
			name, ok := value.(string)
			if !ok || !allowed[name] {
				valid = false
				break
			}
		}
		if valid {
			public[field] = values
		}
	}
	for field, maximum := range map[string]float64{"min_duration_seconds": 15, "max_duration_seconds": 15, "max_video_bytes": 200 * 1024 * 1024, "max_audio_bytes": 15 * 1024 * 1024} {
		if boundedMediaInteger(metadata[field], maximum) {
			public[field] = metadata[field]
		}
	}
	switch kind {
	case "reference_video":
		public["supports_images_with_video"], public["supports_audio_with_video"], public["supports_generate_audio_with_video"] = withImages, withAudios, withGeneratedAudio
	case "reference_audio":
		public["requires_non_audio_input"] = !audioOnly
		public["supports_images_with_audio"], public["supports_video_with_audio"], public["supports_generate_audio_with_reference_audio"] = withImages, withVideos, withGeneratedAudio
	case "reference_video_audio":
		public["max_video_count"], public["max_audio_count"] = maxVideos, maxAudios
		public["supports_images_with_video_audio"], public["supports_generate_audio_with_video_audio"] = withImages, withGeneratedAudio
	}
	return public
}
