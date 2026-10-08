package main

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPricingProjectionKeepsNativeRowsAndQuotesExactSpec(t *testing.T) {
	s, r, _, _ := fixture(t)
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.URL.Path == "/api/status" {
			_, _ = w.Write([]byte(`{"data":{"quota_db_authoritative":true,"enable_batch_update":false}}`))
			return
		}
		require.Equal(t, "/api/pricing", q.URL.Path)
		_, _ = w.Write([]byte(`{"success":true,"pricing_version":"native","group_ratio":{"视频":0.15},"usable_group":{"视频":"video"},"supported_endpoint":{"openai":{"path":"/v1/chat/completions","method":"POST"}},"data":[{"model_name":"old-model","model_price":4.1,"enable_groups":["视频"]}]}`))
	}))
	defer native.Close()
	s.native = native.URL
	w := request(r, "GET", "/api/pricing", "", "")
	require.Equal(t, 200, w.Code, w.Body.String())
	var result map[string]interface{}
	require.NoError(t, common.Unmarshal(w.Body.Bytes(), &result))
	rows := result["data"].([]interface{})
	require.Len(t, rows, 2)
	old := rows[0].(map[string]interface{})
	assert.Equal(t, "old-model", old["model_name"])
	assert.Equal(t, 4.1, old["model_price"])
	added := rows[1].(map[string]interface{})
	assert.Equal(t, "grok-imagine-video-official", added["model_name"])
	assert.InDelta(t, 4.5, added["model_price"], 0.000001)
	exact := added["public_video_pricing"].(map[string]interface{})
	assert.Equal(t, "0.675000", exact["reserved_cny_exact"])
	assert.Equal(t, "480p", exact["resolution"])
	assert.Contains(t, w.Body.String(), "/v1/videos")
	assert.NotContains(t, w.Body.String(), "reference_cost")
	assert.Equal(t, "partial", result["video_catalog_status"])
	assert.Len(t, result["video_catalog_missing"].([]interface{}), 6)
	s.backend = "http://127.0.0.1:1"
	degraded := request(r, "GET", "/api/pricing", "", "")
	require.Equal(t, 200, degraded.Code)
	require.NoError(t, common.Unmarshal(degraded.Body.Bytes(), &result))
	assert.Len(t, result["data"].([]interface{}), 1)
	mismatch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.URL.Path == "/v1/capabilities" {
			_, _ = w.Write([]byte(`{"billing_contract_version":"xtai-video-billing-v2.2","capabilities":{"video":{"traffic_enabled":true,"models":[{"id":"grok-imagine-video-official","available":true}]}}}`))
			return
		}
		_, _ = w.Write([]byte(`{"pricing":{"contract_version":"xtai-video-pricing-v1","models":[{"model":"grok-imagine-video-official","resolution":"480p","currency":"CNY","billing_unit":"output_second","cny_per_second_exact":"99.000000"}]}}`))
	}))
	defer mismatch.Close()
	s.backend = mismatch.URL
	w = request(r, "GET", "/api/pricing", "", "")
	require.Equal(t, 200, w.Code)
	require.NoError(t, common.Unmarshal(w.Body.Bytes(), &result))
	assert.Equal(t, "partial", result["video_catalog_status"])
	assert.Len(t, result["video_catalog_missing"].([]interface{}), 7)
	assert.Len(t, result["data"].([]interface{}), 1)
	empty := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) { _, _ = w.Write([]byte(`{}`)) }))
	defer empty.Close()
	s.backend = empty.URL
	w = request(r, "GET", "/api/pricing", "", "")
	require.Equal(t, 200, w.Code)
	require.NoError(t, common.Unmarshal(w.Body.Bytes(), &result))
	assert.Equal(t, "unavailable", result["video_catalog_status"])
	assert.Len(t, result["data"].([]interface{}), 1)
}

func TestPricingProjectionPublishesOnlyMatchingExactImageSpecification(t *testing.T) {
	s, router, _, _ := fixture(t)
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/status" {
			_, _ = w.Write([]byte(`{"data":{"quota_db_authoritative":true,"enable_batch_update":false}}`))
			return
		}
		_, _ = w.Write([]byte(`{"success":true,"group_ratio":{"视频":0.15},"data":[]}`))
	}))
	t.Cleanup(native.Close)
	s.native = native.URL
	for _, mode := range []string{`"reference"`, `["reference"]`} {
		t.Run(mode, func(t *testing.T) {
			profile := `{"model":"grok-imagine-video-official","operation_mode":MODE,"image_count":1,"duration":1,"resolution":"480p","currency":"CNY","amount_cny_exact":"0.675000","pricing_revision":"verified-images"}`
			profile = strings.ReplaceAll(profile, "MODE", mode)
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/v1/capabilities" {
					_, _ = w.Write([]byte(`{"billing_contract_version":"xtai-video-billing-v2.2","capabilities":{"video":{"traffic_enabled":true,"models":[{"id":"grok-imagine-video-official","available":true,"image_reference":{"available":true,"specifications":[` + profile + `]}}]}}}`))
					return
				}
				_, _ = w.Write([]byte(`{"pricing":{"contract_version":"xtai-video-pricing-v1","models":[{"model":"grok-imagine-video-official","resolution":"480p","currency":"CNY","billing_unit":"output_second","cny_per_second_exact":"0.675000"}]},"image_reference_pricing":{"models":[` + profile + `]}}`))
			}))
			t.Cleanup(backend.Close)
			s.backend = backend.URL
			response := request(router, "GET", "/api/pricing", "", "")
			require.Equal(t, 200, response.Code, response.Body.String())
			if mode == `"reference"` {
				assert.Contains(t, response.Body.String(), `"image_reference"`)
				assert.Contains(t, response.Body.String(), "另支持已验证图片参考")
			} else {
				assert.NotContains(t, response.Body.String(), `"image_reference"`)
			}
		})
	}
}

func TestPricingProjectionPublishesOnlyExactNodyMediaTuplesWithoutPrivateEvidence(t *testing.T) {
	s, router, _, _ := fixture(t)
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/status" {
			_, _ = w.Write([]byte(`{"data":{"quota_db_authoritative":true,"enable_batch_update":false}}`))
			return
		}
		_, _ = w.Write([]byte(`{"success":true,"group_ratio":{"视频":0.15},"data":[{"model_name":"old-model","model_price":4.1}]}`))
	}))
	t.Cleanup(native.Close)
	s.native = native.URL
	for _, mismatch := range []bool{false, true} {
		t.Run(map[bool]string{false: "exact", true: "input_seconds_mismatch"}[mismatch], func(t *testing.T) {
			profile := map[string]interface{}{"model": "wan3.0-video", "operation_mode": "all_reference", "resolution": "480p", "duration": float64(2), "image_count": float64(1), "video_count": float64(1), "audio_count": float64(1), "generate_audio": true, "aspect_ratio": "16:9", "input_video_seconds_exact": "2.000000", "input_audio_seconds_exact": "2.000000", "currency": "CNY", "amount_cny_exact": "1.125000", "pricing_revision": "nody-media-verified", "media_contract_evidence": map[string]interface{}{"task_id": "private-task"}, "reference_cost_cny_exact": "0.750000"}
			price := make(map[string]interface{}, len(profile))
			for key, value := range profile {
				price[key] = value
			}
			if mismatch {
				price["input_audio_seconds_exact"] = "3.000000"
			}
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var result map[string]interface{}
				if r.URL.Path == "/v1/capabilities" {
					result = map[string]interface{}{"billing_contract_version": "xtai-video-billing-v2.2", "capabilities": map[string]interface{}{"video": map[string]interface{}{"traffic_enabled": true, "models": []interface{}{map[string]interface{}{
						"id": "wan3.0-video", "available": true, "media_reference": map[string]interface{}{"available": true, "specifications": []interface{}{profile}},
						"reference_video":       map[string]interface{}{"available": true, "max_count": float64(5), "supports_images_with_video": false, "roles": []interface{}{"reference_video"}, "mime_types": []interface{}{"video/mp4"}, "video_codecs": []interface{}{"h264", "hevc"}, "min_duration_seconds": float64(1), "max_duration_seconds": float64(15), "max_video_bytes": float64(200 * 1024 * 1024), "private_url": "https://private.invalid/video"},
						"reference_audio":       map[string]interface{}{"available": true, "requires_non_audio_input": false, "roles": []interface{}{"reference_audio"}, "mime_types": []interface{}{"audio/mpeg"}, "audio_codecs": []interface{}{"mp3"}, "min_duration_seconds": float64(1), "max_duration_seconds": float64(15), "max_audio_bytes": float64(15 * 1024 * 1024), "media_contract_evidence": "private-evidence"},
						"reference_video_audio": map[string]interface{}{"available": true, "supports_images_with_video_audio": false, "roles": []interface{}{"reference_video", "reference_audio"}},
					}}}}}
				} else {
					result = map[string]interface{}{"pricing": map[string]interface{}{"contract_version": "xtai-video-pricing-v1", "models": []interface{}{map[string]interface{}{"model": "wan3.0-video", "resolution": "480p", "currency": "CNY", "billing_unit": "output_second", "cny_per_second_exact": "0.562500"}}}, "media_reference_pricing": map[string]interface{}{"contract_version": "xtai-video-billing-v2.2", "currency": "CNY", "models": []interface{}{price}}}
				}
				wire, err := common.Marshal(result)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(backend.Close)
			s.backend = backend.URL
			response := request(router, "GET", "/api/pricing", "", "")
			require.Equal(t, 200, response.Code, response.Body.String())
			assert.Contains(t, response.Body.String(), `"model_price":4.1`)
			assert.Contains(t, response.Body.String(), `"reserved_cny_exact":"1.125000"`)
			assert.NotContains(t, response.Body.String(), "private-task")
			assert.NotContains(t, response.Body.String(), "media_contract_evidence")
			assert.NotContains(t, response.Body.String(), "reference_cost_cny_exact")
			assert.NotContains(t, response.Body.String(), "private.invalid")
			assert.NotContains(t, response.Body.String(), "private-evidence")
			if mismatch {
				assert.NotContains(t, response.Body.String(), `"media_reference"`)
				assert.NotContains(t, response.Body.String(), `"reference_video"`)
				assert.NotContains(t, response.Body.String(), `"reference_audio"`)
			} else {
				assert.Contains(t, response.Body.String(), `"media_reference"`)
				assert.Contains(t, response.Body.String(), `"input_audio_seconds_exact":"2.000000"`)
				assert.Contains(t, response.Body.String(), `"operation_mode":"all_reference"`)
				var result map[string]interface{}
				require.NoError(t, common.Unmarshal(response.Body.Bytes(), &result))
				row := result["data"].([]interface{})[1].(map[string]interface{})
				video, ok := row["reference_video"].(map[string]interface{})
				require.True(t, ok)
				assert.Equal(t, float64(1), video["max_count"], "count must come from matched profiles, not an unpriced capability flag")
				assert.Equal(t, true, video["supports_images_with_video"])
				assert.Equal(t, true, video["supports_audio_with_video"])
				assert.Equal(t, []interface{}{"video/mp4"}, video["mime_types"])
				audio := row["reference_audio"].(map[string]interface{})
				assert.Equal(t, true, audio["requires_non_audio_input"], "audio-only availability must be backed by its own matched profile")
				assert.Equal(t, true, audio["supports_video_with_audio"])
				combo := row["reference_video_audio"].(map[string]interface{})
				assert.Equal(t, true, combo["supports_images_with_video_audio"])
				assert.Equal(t, float64(3), combo["max_total_assets"])
			}
		})
	}
}
