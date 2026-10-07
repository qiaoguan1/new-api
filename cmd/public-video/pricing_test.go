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
