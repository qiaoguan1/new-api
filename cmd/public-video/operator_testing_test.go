package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/shopspring/decimal"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func operatorTestingQuote(body map[string]interface{}) map[string]interface{} {
	quote := nodyMediaQuote(body)
	quote["price_source"] = "nodyhub_operator_testing_estimate"
	quote["pricing_kind"] = "estimated_reservation"
	quote["verification_status"] = "unverified"
	quote["admission_mode"] = "operator_testing"
	quote["is_upper_bound"] = false
	quote["reserve_cap_applied"] = false
	quote["policy_digest"] = strings.Repeat("d", 64)
	quote["pricing_revision"] = "nody-manual-test-policy"
	return quote
}

func TestPublicOperatorEstimateFreezesUnverifiedBasisAndRestartStripsEnvelope(t *testing.T) {
	s, router, user, token := fixture(t)
	preflights := 0
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		preflights++
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		wire, err := common.Marshal(operatorTestingQuote(body))
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	wire, err := common.Marshal(nodyMediaBody(t, "operator-estimate"))
	require.NoError(t, err)
	first := request(router, "POST", "/v1/videos", token.Key, string(wire))
	require.Equal(t, 202, first.Code, first.Body.String())
	var result map[string]interface{}
	require.NoError(t, common.Unmarshal(first.Body.Bytes(), &result))
	billing := result["billing"].(map[string]interface{})
	assert.Equal(t, "nodyhub_operator_testing_estimate", billing["reserve_basis"])
	assert.Equal(t, "estimated_reservation", billing["pricing_kind"])
	assert.Equal(t, "unverified", billing["verification_status"])
	assert.Equal(t, false, billing["is_upper_bound"])
	assert.NotEmpty(t, billing["warning"])
	assert.NotContains(t, first.Body.String(), "_public_reservation")
	assert.NotContains(t, first.Body.String(), "media.example")
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	var frozen map[string]interface{}
	require.NoError(t, common.UnmarshalJsonStr(row.Body, &frozen))
	reservation, ok := frozen["_public_reservation"].(map[string]interface{})
	require.True(t, ok, "the pricing provenance must be durable without schema changes")
	assert.Equal(t, "estimated_reservation", reservation["pricing_kind"])
	assert.Equal(t, strings.Repeat("d", 64), reservation["policy_digest"])
	frozenBody := row.Body
	s.backend = "http://127.0.0.1:1"
	replay := request(router, "POST", "/v1/videos", token.Key, strings.ReplaceAll(string(wire), "sig=one", "sig=two"))
	assert.Equal(t, 200, replay.Code, replay.Body.String())
	assert.Equal(t, 1, preflights)
	require.NoError(t, model.DB.First(&row).Error)
	assert.Equal(t, frozenBody, row.Body)
	posts := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/v1/video-jobs/by-request/") {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{}`))
			return
		}
		if r.Method == "POST" {
			require.Equal(t, "/v1/videos", r.URL.Path)
			posts++
			var forwarded map[string]interface{}
			require.NoError(t, common.DecodeJson(r.Body, &forwarded))
			assert.NotContains(t, forwarded, "_public_reservation")
			assert.NotContains(t, forwarded, "policy_digest")
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"vjob_55555555555555555555555555555555","status":"running","billing":{"status":"reserved"}}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"vjob_55555555555555555555555555555555","status":"succeeded","billing":{"status":"settled","currency":"CNY","charged_amount":"1.350000"}}`))
	}))
	t.Cleanup(backend.Close)
	restarted := &server{backend: backend.URL, native: s.native, client: s.client, serviceToken: s.serviceToken, rate: s.rate, publicURL: s.publicURL}
	require.NoError(t, restarted.process(row))
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, restarted.process(row))
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 325000, user.Quota)
	assert.Equal(t, 1, posts)
	final := restarted.snapshot(row)
	finalBilling := final["billing"].(map[string]interface{})
	assert.Equal(t, "nodyhub_operator_testing_estimate", finalBilling["reserve_basis"])
	assert.Equal(t, "1.350000", finalBilling["charged_amount"])
	assert.Equal(t, "0.225000", finalBilling["supplement_amount"])
}

func TestPublicOperatorEstimateRejectsMissingOrForgedProvenanceBeforeDebit(t *testing.T) {
	for _, field := range []string{"price_source", "pricing_kind", "verification_status", "admission_mode", "is_upper_bound", "policy_digest", "reserve_cap_applied"} {
		t.Run(field, func(t *testing.T) {
			s, router, user, token := fixture(t)
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]interface{}
				require.NoError(t, common.DecodeJson(r.Body, &body))
				quote := operatorTestingQuote(body)
				delete(quote, field)
				wire, err := common.Marshal(quote)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			wire, err := common.Marshal(nodyMediaBody(t, "operator-bad-quote"))
			require.NoError(t, err)
			result := request(router, "POST", "/v1/videos", token.Key, string(wire))
			assert.Equal(t, 503, result.Code, result.Body.String())
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			assert.Equal(t, 1000000, user.Quota)
		})
	}
}

func TestPublicLegacyImageSyntaxNeverMislabelsAnOperatorQuoteAsVerified(t *testing.T) {
	s, router, _, token := fixture(t)
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		quote := operatorTestingQuote(body)
		quote["video_count"], quote["audio_count"] = float64(0), float64(0)
		delete(quote, "input_video_seconds_exact")
		delete(quote, "input_audio_seconds_exact")
		quote["reserved_cny_exact"] = "0.450000"
		wire, err := common.Marshal(quote)
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := `{"request_id":"legacy-estimate","model":"grok-imagine-video-official","prompt":"A ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
	result := request(router, "POST", "/v1/videos", token.Key, body)
	require.Equal(t, 202, result.Code, result.Body.String())
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	assert.Contains(t, row.Body, "_public_reservation")
	assert.Equal(t, operatorEstimateSource, s.snapshot(row)["billing"].(map[string]interface{})["reserve_basis"])
	s.backend = "http://127.0.0.1:1"
	assert.Equal(t, 200, request(router, "POST", "/v1/videos", token.Key, body).Code)
}

func TestPublicBroaderTextCandidateNeedsExplicitModeAndVerifiedEnabledPolicy(t *testing.T) {
	s, router, user, token := fixture(t)
	calls := 0
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		quote := operatorTestingQuote(body)
		quote["image_count"], quote["video_count"], quote["audio_count"] = float64(0), float64(0), float64(0)
		delete(quote, "input_video_seconds_exact")
		delete(quote, "input_audio_seconds_exact")
		wire, err := common.Marshal(quote)
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := `{"request_id":"operator-text","model":"wan3.0-video","prompt":"A ball","duration":3,"resolution":"720p","mode":"text"}`
	first := request(router, "POST", "/v1/videos", token.Key, body)
	require.Equal(t, 202, first.Code, first.Body.String())
	assert.Equal(t, 1, calls)
	assert.Equal(t, 400, request(router, "POST", "/v1/videos", token.Key, strings.Replace(body, `,"mode":"text"`, "", 1)).Code)
	assert.Equal(t, 400, request(router, "POST", "/v1/videos", token.Key, strings.Replace(body, `"A ball"`, `" "`, 1)).Code)
	s.backend = "http://127.0.0.1:1"
	assert.Equal(t, 200, request(router, "POST", "/v1/videos", token.Key, body).Code)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 437500, user.Quota)
}

func TestPublicOperatorFrozenProvenanceTamperingNeverRequotesOrSubmits(t *testing.T) {
	s, router, _, token := fixture(t)
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		wire, err := common.Marshal(operatorTestingQuote(body))
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	wire, err := common.Marshal(nodyMediaBody(t, "frozen-integrity"))
	require.NoError(t, err)
	require.Equal(t, 202, request(router, "POST", "/v1/videos", token.Key, string(wire)).Code)
	var original model.PublicVideoTask
	require.NoError(t, model.DB.First(&original).Error)
	calls := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(500) }))
	t.Cleanup(backend.Close)
	s.backend = backend.URL
	for _, field := range []string{"policy_digest", "reserved_cny_exact", "price_source", "generate_audio", "request_id"} {
		t.Run(field, func(t *testing.T) {
			row := original
			var body map[string]interface{}
			require.NoError(t, common.UnmarshalJsonStr(row.Body, &body))
			reservation := body["_public_reservation"].(map[string]interface{})
			reservation[field] = "tampered"
			wire, err := common.Marshal(body)
			require.NoError(t, err)
			row.Body = string(wire)
			assert.Error(t, s.process(row))
			assert.Zero(t, calls)
			assert.NotContains(t, s.snapshot(row)["billing"].(map[string]interface{})["reserve_basis"], "verified")
		})
	}
}

func TestPublicOperatorSupplementWaitsForFundsWithoutRegenerating(t *testing.T) {
	s, router, user, token := fixture(t)
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		wire, err := common.Marshal(operatorTestingQuote(body))
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	wire, err := common.Marshal(nodyMediaBody(t, "operator-supplement"))
	require.NoError(t, err)
	require.Equal(t, 202, request(router, "POST", "/v1/videos", token.Key, string(wire)).Code)
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, model.DB.Model(&row).Updates(map[string]interface{}{"backend_id": "vjob_66666666666666666666666666666666", "state": "submitted"}).Error)
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, model.DB.Model(&user).Update("quota", 10000).Error)
	require.NoError(t, model.DB.Model(&token).Update("remain_quota", 10000).Error)
	queries := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		queries++
		require.Equal(t, "GET", r.Method)
		require.Equal(t, "/v1/videos/"+row.BackendID, r.URL.Path)
		_, _ = w.Write([]byte(`{"id":"vjob_66666666666666666666666666666666","status":"succeeded","billing":{"status":"settled","currency":"CNY","charged_amount":"1.350000"}}`))
	}))
	t.Cleanup(backend.Close)
	s.backend = backend.URL
	require.ErrorIs(t, s.process(row), model.ErrPublicVideoQuota)
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 10000, user.Quota)
	assert.Equal(t, "pending_review", row.State)
	assert.Equal(t, "wallet_supplement_required", row.LastError)
	assert.Contains(t, row.Snapshot, "1.350000")
	assert.Empty(t, row.ChargedCNY)
	view := s.snapshot(row)
	assert.Equal(t, "pending_review", view["status"])
	assert.Contains(t, view["error"].(map[string]interface{})["message"], "余额")
	assert.Equal(t, "0.225000", view["billing"].(map[string]interface{})["required_supplement_amount"])
	require.NoError(t, model.DB.Model(&user).Update("quota", 200000).Error)
	require.NoError(t, model.DB.Model(&token).Update("remain_quota", 200000).Error)
	require.NoError(t, s.process(row))
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 87500, user.Quota)
	assert.Equal(t, "settled", row.State)
	assert.Empty(t, row.LastError)
	assert.Equal(t, 2, queries)
	assert.NotContains(t, s.snapshot(row), "error")
}

func operatorTestingRule(name string) map[string]interface{} {
	limits := nodyMediaCandidateLimits[name]
	rule := map[string]interface{}{
		"model": name, "operation_modes": []interface{}{"text", "reference", "all_reference"}, "resolutions": []interface{}{"720p"}, "durations": []interface{}{float64(6), float64(10)}, "duration_min": float64(6), "duration_max": float64(10), "aspect_ratios": []interface{}{"16:9"},
		"max_images": float64(limits.images), "max_videos": float64(limits.videos), "max_audios": float64(limits.audios), "max_total_assets": float64(limits.images + limits.videos + limits.audios), "generate_audio_values": []interface{}{true},
		"input_video":                           map[string]interface{}{"max_count": float64(limits.videos), "min_duration_seconds": float64(1), "max_duration_seconds": float64(15), "max_total_duration_seconds": float64(limits.videos * 15)},
		"input_audio":                           map[string]interface{}{"max_count": float64(limits.audios), "min_duration_seconds": float64(1), "max_duration_seconds": float64(15), "max_total_duration_seconds": float64(limits.audios * 15)},
		"estimated_cny_per_output_second_exact": "3.600000", "max_reserve_cny_exact": "150.000000", "currency": "CNY", "pricing_revision": "manual-policy", "price_source": "nodyhub_operator_testing_estimate", "pricing_kind": "estimated_reservation", "verification_status": "unverified", "admission_mode": "operator_testing", "is_upper_bound": false,
		"operator_policy_evidence": map[string]interface{}{"private_bill": "do-not-publish"}, "upstream_cost": "private-cost", "asset_url": "https://private.invalid/source", "asset_sha256": strings.Repeat("f", 64),
	}
	if name == "omni-flash" {
		rule["image_input_counts"] = []interface{}{float64(0), float64(1), float64(3)}
		rule["video_input_output_duration_min"], rule["video_input_output_duration_max"] = float64(4), float64(30)
	}
	return rule
}

func TestOperatorOmniEditingRangeIsExplicitWithoutBroadeningTextDurations(t *testing.T) {
	rule := operatorTestingRule("omni-flash")
	public, ok := publicOperatorRule("omni-flash", rule)
	require.True(t, ok)
	assert.Equal(t, float64(4), public["video_input_output_duration_min"])
	assert.Equal(t, float64(30), public["video_input_output_duration_max"])
	assert.Equal(t, rule["durations"], public["durations"])
	assert.Equal(t, rule["duration_max"], public["duration_max"])
	for _, field := range []string{"video_input_output_duration_min", "video_input_output_duration_max"} {
		for _, mutation := range []string{"missing", "wrong_type", "wrong_bound"} {
			t.Run(field+"/"+mutation, func(t *testing.T) {
				changed := operatorTestingRule("omni-flash")
				switch mutation {
				case "missing":
					delete(changed, field)
				case "wrong_type":
					changed[field] = "4"
				case "wrong_bound":
					changed[field] = float64(31)
				}
				_, accepted := publicOperatorRule("omni-flash", changed)
				assert.False(t, accepted)
			})
		}
	}
	wan := operatorTestingRule("wan3.0-video")
	wan["video_input_output_duration_min"], wan["video_input_output_duration_max"] = float64(4), float64(30)
	public, ok = publicOperatorRule("wan3.0-video", wan)
	require.True(t, ok)
	assert.NotContains(t, public, "video_input_output_duration_min")
	assert.NotContains(t, public, "video_input_output_duration_max")
}

func TestMarketplaceProjectsAllSevenOperatorCandidatesAsUnverifiedRulesNotExactProfiles(t *testing.T) {
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
		t.Run(map[bool]string{false: "enabled", true: "rules_mismatch"}[mismatch], func(t *testing.T) {
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				caps, prices, rules := []interface{}{}, []interface{}{}, []interface{}{}
				for name, spec := range specs {
					rule := operatorTestingRule(name)
					capRule := operatorTestingRule(name)
					if mismatch {
						capRule["estimated_cny_per_output_second_exact"] = "4.000000"
					}
					caps = append(caps, map[string]interface{}{"id": name, "available": true, "operator_testing": map[string]interface{}{"supported": true, "available": true, "verification_status": "unverified", "admission_mode": "operator_testing", "rules": []interface{}{capRule}},
						"reference_video": map[string]interface{}{"available": false, "roles": []interface{}{"reference_video"}, "mime_types": []interface{}{"video/mp4"}, "video_codecs": []interface{}{"h264", "hevc"}, "max_video_bytes": float64(200 * 1024 * 1024)},
						"reference_audio": map[string]interface{}{"available": false, "roles": []interface{}{"reference_audio"}, "mime_types": []interface{}{"audio/mpeg"}, "audio_codecs": []interface{}{"mp3"}, "max_audio_bytes": float64(15 * 1024 * 1024)},
					})
					amount, err := decimal.NewFromString(spec.Reserve)
					require.NoError(t, err)
					prices = append(prices, map[string]interface{}{"model": name, "resolution": spec.Resolution, "currency": "CNY", "billing_unit": "output_second", "cny_per_second_exact": amount.Div(decimal.NewFromInt(int64(spec.Duration))).StringFixed(6)})
					rules = append(rules, rule)
				}
				var result map[string]interface{}
				if r.URL.Path == "/v1/capabilities" {
					result = map[string]interface{}{"billing_contract_version": "xtai-video-billing-v2.2", "capabilities": map[string]interface{}{"video": map[string]interface{}{"traffic_enabled": true, "models": caps}}}
				} else {
					result = map[string]interface{}{"pricing": map[string]interface{}{"contract_version": "xtai-video-pricing-v1", "models": prices}, "operator_testing": map[string]interface{}{"enabled": true, "verification_status": "unverified", "admission_mode": "operator_testing", "rules": rules}}
				}
				wire, err := common.Marshal(result)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(backend.Close)
			s.backend = backend.URL
			response := request(router, "GET", "/api/pricing", "", "")
			require.Equal(t, 200, response.Code, response.Body.String())
			var result map[string]interface{}
			require.NoError(t, common.Unmarshal(response.Body.Bytes(), &result))
			rows := result["data"].([]interface{})
			require.Len(t, rows, 8)
			assert.Equal(t, float64(4.1), rows[0].(map[string]interface{})["model_price"])
			for _, value := range rows[1:] {
				row := value.(map[string]interface{})
				if mismatch {
					assert.NotContains(t, row, "operator_testing")
					continue
				}
				operator, ok := row["operator_testing"].(map[string]interface{})
				require.True(t, ok)
				assert.Equal(t, "unverified", operator["verification_status"])
				assert.Equal(t, "estimated_reservation", operator["pricing_kind"])
				assert.Equal(t, false, operator["is_upper_bound"])
				assert.Contains(t, row["description"], "未验收手测")
				assert.NotContains(t, operator, "specifications")
				assert.Len(t, operator["rules"].([]interface{}), 1)
				name := row["model_name"].(string)
				limits := nodyMediaCandidateLimits[name]
				if limits.videos > 0 {
					video, ok := row["reference_video"].(map[string]interface{})
					require.True(t, ok, "operator video candidates must be visible without fake exact profiles")
					assert.Equal(t, true, video["available"])
					assert.Equal(t, float64(limits.videos), video["max_count"])
					assert.Equal(t, "unverified", video["verification_status"])
					assert.Equal(t, "operator_testing", video["admission_mode"])
					assert.Equal(t, operatorEstimateSource, video["price_source"])
					assert.Equal(t, []interface{}{"video/mp4"}, video["mime_types"])
				}
				if limits.audios > 0 {
					audio := row["reference_audio"].(map[string]interface{})
					assert.Equal(t, true, audio["available"])
					assert.Equal(t, false, audio["requires_non_audio_input"])
					assert.Equal(t, "unverified", audio["verification_status"])
					combo := row["reference_video_audio"].(map[string]interface{})
					assert.Equal(t, true, combo["available"])
					assert.Empty(t, combo["specifications"])
				}
			}
			for _, private := range []string{"do-not-publish", "private-cost", "private.invalid", strings.Repeat("f", 64), "operator_policy_evidence"} {
				assert.NotContains(t, response.Body.String(), private)
			}
		})
	}
}
