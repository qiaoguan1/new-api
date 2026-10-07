package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func nodyMediaBody(t *testing.T, requestID string) map[string]interface{} {
	t.Helper()
	return map[string]interface{}{
		"request_id": requestID, "model": "wan3.0-video", "prompt": "A blue ball", "duration": float64(2), "resolution": "480p",
		"mode": "all_reference", "aspect_ratio": "16:9", "generate_audio": true,
		"reference_images": []interface{}{map[string]interface{}{"url": "https://media.example/a.png?sig=one", "role": "reference", "sha256": strings.Repeat("a", 64)}},
		"reference_videos": []interface{}{map[string]interface{}{"url": "https://media.example/a.mp4?sig=one", "role": "reference_video", "sha256": strings.Repeat("b", 64),
			"size_bytes": float64(10000), "duration_seconds": "2.000000", "mime_type": "video/mp4", "width_pixels": float64(1280), "height_pixels": float64(720)}},
		"reference_audios": []interface{}{map[string]interface{}{"url": "https://media.example/a.mp3?sig=one", "role": "reference_audio", "sha256": strings.Repeat("c", 64),
			"size_bytes": float64(5000), "duration_seconds": "2.000000", "mime_type": "audio/mpeg", "codec": "mp3", "sample_rate_hz": float64(44100), "channels": float64(2)}},
	}
}

func nodyMediaQuote(body map[string]interface{}) map[string]interface{} {
	return map[string]interface{}{"ok": true, "billing_contract_version": "xtai-video-billing-v2.2", "task_created": false, "upstream_submitted": false,
		"model": body["model"], "mode": body["mode"], "operation_mode": body["mode"], "resolution": body["resolution"], "duration": body["duration"],
		"image_count": float64(1), "video_count": float64(1), "audio_count": float64(1), "generate_audio": body["generate_audio"], "aspect_ratio": body["aspect_ratio"],
		"input_video_seconds_exact": "2.000000", "input_audio_seconds_exact": "2.000000", "currency": "CNY", "reserved_cny_exact": "1.125000", "pricing_revision": "verified-nody-media"}
}

func TestPublicNodyMixedMediaVerifiedBeforeDebitAndFrozenURLFreeReplay(t *testing.T) {
	s, router, user, token := fixture(t)
	verifications := 0
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer "+s.serviceToken, r.Header.Get("Authorization"))
		require.Equal(t, "/v1/operations/video-input-validation", r.URL.Path)
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		assert.NotContains(t, body, "reference_images")
		assert.Equal(t, []interface{}{"reference"}, body["image_roles"])
		assert.Equal(t, []interface{}{strings.Repeat("a", 64)}, body["image_identities"])
		require.NoError(t, model.DB.First(&user, user.Id).Error)
		assert.Equal(t, 1000000, user.Quota, "media verification must precede wallet reservation")
		verifications++
		quote, err := common.Marshal(nodyMediaQuote(body))
		require.NoError(t, err)
		_, _ = w.Write(quote)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := nodyMediaBody(t, "nody-mixed")
	wire, err := common.Marshal(body)
	require.NoError(t, err)
	first := request(router, "POST", "/v1/videos", token.Key, string(wire))
	require.Equal(t, 202, first.Code, first.Body.String())
	assert.NotContains(t, first.Body.String(), "media.example")
	assert.NotContains(t, first.Body.String(), "media_contract_evidence")
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	assert.Equal(t, "1.125000", row.ReservedCNY)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 437500, user.Quota)
	frozen := row.Body
	replay := strings.ReplaceAll(string(wire), "sig=one", "sig=two")
	s.backend = "http://127.0.0.1:1"
	assert.Equal(t, 200, request(router, "POST", "/v1/videos", token.Key, replay).Code)
	assert.Equal(t, 1, verifications)
	require.NoError(t, model.DB.First(&row).Error)
	assert.Equal(t, frozen, row.Body)
	changed := strings.Replace(replay, strings.Repeat("c", 64), strings.Repeat("d", 64), 1)
	assert.Equal(t, 409, request(router, "POST", "/v1/videos", token.Key, changed).Code)
	changed = strings.Replace(replay, `"duration_seconds":"2.000000"`, `"duration_seconds":"3.000000"`, 1)
	assert.Equal(t, 409, request(router, "POST", "/v1/videos", token.Key, changed).Code)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 437500, user.Quota)
}

func TestPublicNodyMediaQuoteMismatchNeverReserves(t *testing.T) {
	for _, field := range []string{"operation_mode", "generate_audio", "aspect_ratio", "image_count", "video_count", "audio_count", "input_video_seconds_exact", "input_audio_seconds_exact", "currency", "pricing_revision", "reserved_cny_exact"} {
		t.Run(field, func(t *testing.T) {
			s, router, user, token := fixture(t)
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]interface{}
				require.NoError(t, common.DecodeJson(r.Body, &body))
				quote := nodyMediaQuote(body)
				delete(quote, field)
				wire, err := common.Marshal(quote)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			wire, err := common.Marshal(nodyMediaBody(t, "bad-quote"))
			require.NoError(t, err)
			result := request(router, "POST", "/v1/videos", token.Key, string(wire))
			assert.Equal(t, 503, result.Code, result.Body.String())
			var count int64
			require.NoError(t, model.DB.Model(&model.PublicVideoTask{}).Count(&count).Error)
			assert.Zero(t, count)
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			assert.Equal(t, 1000000, user.Quota)
		})
	}
}

func TestPublicNodyFrameRolesNormalizeAndInvalidDescriptorsNeverReachGateway(t *testing.T) {
	for _, mode := range []string{"first_frame", "last_frame", "first_last_frame"} {
		t.Run(mode, func(t *testing.T) {
			s, router, _, token := fixture(t)
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]interface{}
				require.NoError(t, common.DecodeJson(r.Body, &body))
				roles := []interface{}{"first"}
				if mode == "last_frame" {
					roles = []interface{}{"last"}
				}
				if mode == "first_last_frame" {
					roles = []interface{}{"first", "last"}
				}
				assert.Equal(t, roles, body["image_roles"])
				quote := nodyMediaQuote(body)
				quote["image_count"] = float64(len(roles))
				quote["video_count"], quote["audio_count"] = float64(0), float64(0)
				delete(quote, "input_video_seconds_exact")
				delete(quote, "input_audio_seconds_exact")
				wire, err := common.Marshal(quote)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			body := nodyMediaBody(t, mode)
			body["mode"] = mode
			delete(body, "reference_videos")
			delete(body, "reference_audios")
			images := body["reference_images"].([]interface{})
			images[0].(map[string]interface{})["role"] = "first"
			if mode == "last_frame" {
				images[0].(map[string]interface{})["role"] = "last"
			}
			if mode == "first_last_frame" {
				body["reference_images"] = append(images, map[string]interface{}{"url": "https://media.example/last.png", "role": "last", "sha256": strings.Repeat("d", 64)})
			}
			wire, err := common.Marshal(body)
			require.NoError(t, err)
			result := request(router, "POST", "/v1/videos", token.Key, string(wire))
			assert.Equal(t, 202, result.Code, result.Body.String())
		})
	}
	for _, mutation := range []string{"double_images", "private_url", "identity", "duration_number", "metadata_unknown", "oversize", "role", "caller_quote"} {
		t.Run(mutation, func(t *testing.T) {
			s, router, user, token := fixture(t)
			calls := 0
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(500) }))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			body := nodyMediaBody(t, mutation)
			video := body["reference_videos"].([]interface{})[0].(map[string]interface{})
			switch mutation {
			case "double_images":
				body["images"] = []interface{}{"https://media.example/a.png"}
			case "private_url":
				video["url"] = "https://127.0.0.1/a.mp4"
			case "identity":
				video["sha256"] = "bad"
			case "duration_number":
				video["duration_seconds"] = float64(2)
			case "metadata_unknown":
				video["pricing_revision"] = "caller-controlled"
			case "oversize":
				video["size_bytes"] = float64(201 * 1024 * 1024)
			case "role":
				video["role"] = "reference_audio"
			case "caller_quote":
				body["media_contract_evidence"] = map[string]interface{}{"task_id": "private"}
			}
			wire, err := common.Marshal(body)
			require.NoError(t, err)
			assert.Equal(t, 400, request(router, "POST", "/v1/videos", token.Key, string(wire)).Code)
			assert.Zero(t, calls)
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			assert.Equal(t, 1000000, user.Quota)
		})
	}
}

func TestPublicOmniVideoUsesVerifiedSpecAndPreservesOwnerIsolation(t *testing.T) {
	s, router, user, token := fixture(t)
	body := nodyMediaBody(t, "omni-video")
	body["model"], body["mode"], body["duration"], body["resolution"] = "omni-flash", "reference", float64(4), "720p"
	delete(body, "reference_images")
	delete(body, "reference_audios")
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var normalized map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &normalized))
		quote := nodyMediaQuote(normalized)
		quote["image_count"], quote["audio_count"] = float64(0), float64(0)
		delete(quote, "input_audio_seconds_exact")
		wire, err := common.Marshal(quote)
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	wire, err := common.Marshal(body)
	require.NoError(t, err)
	result := request(router, "POST", "/v1/videos", token.Key, string(wire))
	require.Equal(t, 202, result.Code, result.Body.String())
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	other := model.User{Username: "other-media", AffCode: "other-media", Password: "unused", Status: 1, Quota: 1000000}
	require.NoError(t, model.DB.Create(&other).Error)
	otherToken := model.Token{UserId: other.Id, Key: "other-media-key", Status: 1, ExpiredTime: -1, RemainQuota: 1000000}
	require.NoError(t, model.DB.Create(&otherToken).Error)
	assert.Equal(t, 404, request(router, "GET", "/v1/videos/"+row.ID, otherToken.Key, "").Code)
	assert.Equal(t, 404, request(router, "GET", "/v1/videos/"+row.ID+"/content", otherToken.Key, "").Code)
	require.NoError(t, model.DB.Model(&token).Updates(map[string]interface{}{"model_limits_enabled": true, "model_limits": "other-model"}).Error)
	assert.Equal(t, 403, request(router, "POST", "/v1/videos", token.Key, string(wire)).Code)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 437500, user.Quota)
}

func TestPublicNodyMediaDefiniteRefusalRefundsAndUncertainReplayKeepsHold(t *testing.T) {
	for _, mode := range []string{"first_frame", "last_frame", "first_last_frame", "all_reference"} {
		for _, uncertain := range []bool{false, true} {
			t.Run(mode+map[bool]string{false: "/definite", true: "/uncertain"}[uncertain], func(t *testing.T) {
				s, _, user, token := fixture(t)
				body := `{"request_id":"public-nody-refusal","model":"wan3.0-video","mode":"` + mode + `"}`
				row, _, err := model.ReservePublicVideo(model.PublicVideoTask{ID: "vjob_44444444444444444444444444444444", UserID: user.Id, TokenID: token.Id,
					ClientRequestID: "nody-refusal", Fingerprint: strings.Repeat("a", 64), Model: "wan3.0-video", Group: "default", Body: body, ReservedCNY: "1.125000", QuotaPerCNY: "500000"})
				require.NoError(t, err)
				posts := 0
				backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method == "GET" {
						w.WriteHeader(404)
						_, _ = w.Write([]byte(`{}`))
						return
					}
					posts++
					if uncertain && posts == 1 {
						_, _ = w.Write([]byte(`{"invalid":`))
						return
					}
					w.WriteHeader(400)
					_, _ = w.Write([]byte(`{"task_created":false,"upstream_submitted":false,"billing_contract_version":"xtai-video-billing-v2.2","request_id":"public-nody-refusal","error":{"code":"reference_media_expired","phase":"validate"}}`))
				}))
				t.Cleanup(backend.Close)
				s.backend = backend.URL
				if uncertain {
					require.Error(t, s.process(row))
					require.NoError(t, model.DB.First(&row).Error)
				}
				require.NoError(t, s.process(row))
				require.NoError(t, model.DB.First(&row).Error)
				require.NoError(t, model.DB.First(&user, user.Id).Error)
				if uncertain {
					assert.Equal(t, "pending_review", row.State)
					assert.Equal(t, 437500, user.Quota)
					assert.Empty(t, row.ChargedCNY)
				} else {
					assert.Equal(t, "settled", row.State)
					assert.Equal(t, 1000000, user.Quota)
					assert.Equal(t, "0.000000", row.ChargedCNY)
					assert.Contains(t, row.Snapshot, "media_input_rejected_before_creation")
				}
			})
		}
	}
}

func TestPublicNodyCandidateLimitsAreModelSpecificNotSeedanceLimits(t *testing.T) {
	for _, test := range []struct {
		name, model, seconds   string
		images, videos, audios int
		valid                  bool
	}{
		{"wan_full_limits", "wan3.0-video", "15.000000", 10, 5, 5, true},
		{"prime_one_second_audio_only", "wan3.0-video-prime", "1.000000", 0, 0, 1, true},
		{"wan_six_videos", "wan3.0-video", "1.000000", 0, 6, 0, false},
		{"wan_six_audios", "wan3.0-video", "1.000000", 0, 0, 6, false},
		{"wan_eleven_images", "wan3.0-video", "1.000000", 11, 0, 0, false},
		{"omni_three_images_video", "omni-flash", "1.000000", 3, 1, 0, true},
		{"omni_two_images", "omni-flash", "1.000000", 2, 0, 0, false},
		{"omni_two_videos", "omni-flash", "1.000000", 0, 2, 0, false},
		{"omni_audio", "omni-flash", "1.000000", 1, 0, 1, false},
		{"flux_ten_images", "flux-3-video", "1.000000", 10, 0, 0, true},
		{"flux_video", "flux-3-video", "1.000000", 1, 1, 0, false},
		{"grok_video", "grok-video-3", "1.000000", 1, 1, 0, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			body := nodyMediaBody(t, test.name)
			body["model"] = test.model
			for field, count := range map[string]int{"reference_images": test.images, "reference_videos": test.videos, "reference_audios": test.audios} {
				if count == 0 {
					delete(body, field)
					continue
				}
				base := body[field].([]interface{})[0].(map[string]interface{})
				items := make([]interface{}, count)
				for index := range items {
					item := make(map[string]interface{}, len(base))
					for key, value := range base {
						item[key] = value
					}
					if field != "reference_images" {
						item["duration_seconds"] = test.seconds
					}
					items[index] = item
				}
				body[field] = items
			}
			_, err := normalizeNodyMediaInput(body)
			if test.valid {
				assert.NoError(t, err)
			} else {
				assert.Error(t, err)
			}
		})
	}
}

func TestPublicGrokEmptyReferenceArraysKeepLegacyAdmissionAndFingerprint(t *testing.T) {
	s, router, _, token := fixture(t)
	verifications := 0
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &body))
		assert.NotContains(t, body, "reference_videos")
		assert.NotContains(t, body, "reference_audios")
		verifications++
		_, _ = w.Write([]byte(`{"ok":true,"billing_contract_version":"xtai-video-billing-v2.2","task_created":false,"upstream_submitted":false,"model":"grok-imagine-video-official","mode":"reference","image_count":1,"duration":1,"resolution":"480p","reserved_cny_exact":"0.450000","pricing_revision":"verified-images"}`))
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := `{"request_id":"grok-empty-av","model":"grok-imagine-video-official","prompt":"A ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
	withEmptyArrays := strings.Replace(body, `"mode":"reference"`, `"mode":"reference","reference_videos":[],"reference_audios":[]`, 1)
	first := request(router, "POST", "/v1/videos", token.Key, withEmptyArrays)
	require.Equal(t, 202, first.Code, first.Body.String())
	result := request(router, "POST", "/v1/videos", token.Key, body)
	assert.Equal(t, 200, result.Code, result.Body.String())
	assert.Equal(t, 1, verifications)
}

func TestPublicWiderGrokImageProfilesRequireCompleteMediaQuote(t *testing.T) {
	s, router, user, token := fixture(t)
	body := nodyMediaBody(t, "grok-wider")
	body["model"], body["duration"], body["resolution"] = "grok-imagine-1.5-video", float64(6), "480p"
	delete(body, "reference_videos")
	delete(body, "reference_audios")
	image := body["reference_images"].([]interface{})[0]
	body["reference_images"] = []interface{}{image, image, image}
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var normalized map[string]interface{}
		require.NoError(t, common.DecodeJson(r.Body, &normalized))
		// A legacy-shaped quote is incomplete for an additional media profile.
		quote := map[string]interface{}{"ok": true, "billing_contract_version": "xtai-video-billing-v2.2", "task_created": false, "upstream_submitted": false, "model": normalized["model"], "mode": normalized["mode"], "image_count": float64(3), "duration": float64(6), "resolution": "480p", "reserved_cny_exact": "1.350000", "pricing_revision": "wide-profile"}
		wire, err := common.Marshal(quote)
		require.NoError(t, err)
		_, _ = w.Write(wire)
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	wire, err := common.Marshal(body)
	require.NoError(t, err)
	result := request(router, "POST", "/v1/videos", token.Key, string(wire))
	assert.Equal(t, 503, result.Code, result.Body.String())
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 1000000, user.Quota)
}

func TestPublicNodyRetailQuoteBoundaryReservesExactlyWithoutOverflow(t *testing.T) {
	for _, amount := range []string{"150.000000", "150.000001"} {
		t.Run(amount, func(t *testing.T) {
			s, router, user, token := fixture(t)
			require.NoError(t, model.DB.Model(&user).Update("quota", 100000000).Error)
			require.NoError(t, model.DB.Model(&token).Update("remain_quota", 100000000).Error)
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]interface{}
				require.NoError(t, common.DecodeJson(r.Body, &body))
				quote := nodyMediaQuote(body)
				quote["reserved_cny_exact"] = amount
				wire, err := common.Marshal(quote)
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			wire, err := common.Marshal(nodyMediaBody(t, "retail-boundary"))
			require.NoError(t, err)
			result := request(router, "POST", "/v1/videos", token.Key, string(wire))
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			require.NoError(t, model.DB.First(&token, token.Id).Error)
			if amount == "150.000000" {
				assert.Equal(t, 202, result.Code, result.Body.String())
				assert.Equal(t, 25000000, user.Quota)
				assert.Equal(t, 25000000, token.RemainQuota)
				var row model.PublicVideoTask
				require.NoError(t, model.DB.First(&row).Error)
				assert.Equal(t, 75000000, row.ReservedQuota)
			} else {
				assert.Equal(t, 503, result.Code, result.Body.String())
				assert.Equal(t, 100000000, user.Quota)
				assert.Equal(t, 100000000, token.RemainQuota)
				var count int64
				require.NoError(t, model.DB.Model(&model.PublicVideoTask{}).Count(&count).Error)
				assert.Zero(t, count)
			}
		})
	}
}

func TestPublicWanMediaOnlyPromptIsFrozenButOtherModelsStillRequirePrompt(t *testing.T) {
	for _, name := range []string{"wan3.0-video", "wan3.0-video-prime", "omni-flash", "flux-3-video", "grok-imagine-1.5-video"} {
		t.Run(name, func(t *testing.T) {
			s, router, _, token := fixture(t)
			calls := 0
			validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				var body map[string]interface{}
				require.NoError(t, common.DecodeJson(r.Body, &body))
				assert.Equal(t, "", body["prompt"])
				wire, err := common.Marshal(nodyMediaQuote(body))
				require.NoError(t, err)
				_, _ = w.Write(wire)
			}))
			t.Cleanup(validator.Close)
			s.backend = validator.URL
			body := nodyMediaBody(t, "media-only")
			body["model"], body["prompt"] = name, "  "
			if name == "omni-flash" {
				delete(body, "reference_audios")
				body["duration"], body["resolution"] = float64(4), "720p"
			}
			if name == "flux-3-video" || name == "grok-imagine-1.5-video" {
				delete(body, "reference_videos")
				delete(body, "reference_audios")
				body["duration"], body["resolution"] = float64(5), "720p"
			}
			wire, err := common.Marshal(body)
			require.NoError(t, err)
			result := request(router, "POST", "/v1/videos", token.Key, string(wire))
			if strings.HasPrefix(name, "wan3.0-video") {
				assert.Equal(t, 202, result.Code, result.Body.String())
				assert.Equal(t, 1, calls)
				var row model.PublicVideoTask
				require.NoError(t, model.DB.First(&row).Error)
				var frozen map[string]interface{}
				require.NoError(t, common.UnmarshalJsonStr(row.Body, &frozen))
				assert.Equal(t, "", frozen["prompt"])
			} else {
				assert.Equal(t, 400, result.Code, result.Body.String())
				assert.Zero(t, calls)
			}
		})
	}
}

func TestPublicAllSixDeployedGrokImageProfilesKeepLegacyAdmissionAndFingerprint(t *testing.T) {
	for _, profile := range []struct {
		name, resolution string
		duration         int
	}{
		{"grok-video-3", "720p", 6}, {"grok-imagine-1.5-video", "720p", 6}, {"grok-imagine-video-official", "480p", 1},
	} {
		for _, count := range []int{1, 2} {
			t.Run(fmt.Sprintf("%s/%d", profile.name, count), func(t *testing.T) {
				s, router, _, token := fixture(t)
				verifications := 0
				validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					var body map[string]interface{}
					require.NoError(t, common.DecodeJson(r.Body, &body))
					verifications++
					quote := map[string]interface{}{"ok": true, "billing_contract_version": "xtai-video-billing-v2.2", "task_created": false, "upstream_submitted": false, "model": profile.name, "mode": body["mode"], "image_count": float64(count), "duration": float64(profile.duration), "resolution": profile.resolution, "reserved_cny_exact": "0.450000", "pricing_revision": "legacy-six-images"}
					wire, err := common.Marshal(quote)
					require.NoError(t, err)
					_, _ = w.Write(wire)
				}))
				t.Cleanup(validator.Close)
				s.backend = validator.URL
				body := nodyMediaBody(t, "legacy-images")
				delete(body, "reference_images")
				delete(body, "reference_videos")
				delete(body, "reference_audios")
				body["model"], body["resolution"], body["duration"] = profile.name, profile.resolution, float64(profile.duration)
				body["mode"] = "reference"
				if count == 2 {
					body["mode"] = "all_reference"
				}
				images, roles, identities := make([]interface{}, count), make([]interface{}, count), make([]interface{}, count)
				stable := make([]interface{}, count)
				for index := range images {
					images[index] = fmt.Sprintf("https://media.example/%d.png?sig=one", index)
					roles[index] = "reference"
					identities[index] = strings.Repeat("a", 64)
					stable[index] = map[string]interface{}{"role": "reference", "identity": identities[index]}
				}
				body["images"], body["image_roles"], body["image_identities"] = images, roles, identities
				wire, err := common.Marshal(body)
				require.NoError(t, err)
				first := request(router, "POST", "/v1/videos", token.Key, string(wire))
				require.Equal(t, 202, first.Code, first.Body.String())
				var row model.PublicVideoTask
				require.NoError(t, model.DB.First(&row).Error)
				legacyIdentity := map[string]interface{}{"model": profile.name, "resolution": profile.resolution, "duration": float64(profile.duration), "prompt": "A blue ball", "mode": body["mode"], "images": stable, "aspect_ratio": "16:9", "generate_audio": true, "provider_id": "video-aixingtu-api"}
				canonical, err := common.Marshal(legacyIdentity)
				require.NoError(t, err)
				fingerprint := sha256.Sum256(canonical)
				assert.Equal(t, hex.EncodeToString(fingerprint[:]), row.Fingerprint)
				frozen := row.Body
				s.backend = "http://127.0.0.1:1"
				replay := request(router, "POST", "/v1/videos", token.Key, strings.ReplaceAll(string(wire), "sig=one", "sig=two"))
				assert.Equal(t, 200, replay.Code, replay.Body.String())
				assert.Equal(t, 1, verifications)
				require.NoError(t, model.DB.First(&row).Error)
				assert.Equal(t, frozen, row.Body)
			})
		}
	}
}
