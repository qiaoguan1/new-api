package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPublicImageReferenceValidatesBeforeDebitAndKeepsStableReplay(t *testing.T) {
	s, router, user, token := fixture(t)
	verifications := 0
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "Bearer "+s.serviceToken, r.Header.Get("Authorization"))
		require.Equal(t, "/v1/operations/video-input-validation", r.URL.Path)
		verifications++
		_, _ = w.Write([]byte(`{"ok":true,"billing_contract_version":"xtai-video-billing-v2.2","task_created":false,"upstream_submitted":false,"model":"grok-imagine-video-official","mode":"reference","image_count":1,"duration":1,"resolution":"480p","reserved_cny_exact":"0.450000","pricing_revision":"verified-images"}`))
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := `{"request_id":"image-one","model":"grok-imagine-video-official","prompt":"A blue ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png?sig=one"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
	first := request(router, "POST", "/v1/videos", token.Key, body)
	require.Equal(t, 202, first.Code, first.Body.String())
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 775000, user.Quota)
	assert.Equal(t, 1, verifications)
	replay := request(router, "POST", "/v1/videos", token.Key, strings.Replace(body, "sig=one", "sig=two", 1))
	assert.Equal(t, 200, replay.Code, replay.Body.String())
	assert.Equal(t, 1, verifications)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 775000, user.Quota)
	assert.Equal(t, 409, request(router, "POST", "/v1/videos", token.Key, strings.Replace(body, strings.Repeat("a", 64), strings.Repeat("b", 64), 1)).Code)
}

func TestPublicImageReferenceInvalidOrUnavailableCreatesNoTaskOrDebit(t *testing.T) {
	s, router, user, token := fixture(t)
	validator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"task_created":false,"upstream_submitted":false,"error":{"code":"video_image_identity_mismatch"}}`))
	}))
	t.Cleanup(validator.Close)
	s.backend = validator.URL
	body := `{"request_id":"image-invalid","model":"grok-imagine-video-official","prompt":"A blue ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
	for _, changed := range []string{body, strings.Replace(body, "https://media.example", "https://127.0.0.1", 1),
		strings.Replace(body, `"image_roles":["reference"]`, `"image_roles":["first"]`, 1),
		strings.Replace(body, `"mode":"reference"`, `"mode":"first_last_frame"`, 1),
		strings.Replace(body, `"duration":1`, `"duration":1.5`, 1)} {
		response := request(router, "POST", "/v1/videos", token.Key, changed)
		assert.Equal(t, 400, response.Code, response.Body.String())
	}
	var count int64
	require.NoError(t, model.DB.Model(&model.PublicVideoTask{}).Count(&count).Error)
	assert.Zero(t, count)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 1000000, user.Quota)
	require.NoError(t, model.DB.First(&token, token.Id).Error)
	assert.Equal(t, 1000000, token.RemainQuota)
}

func TestExplicitTextModePreservesHistoricalRequestFingerprint(t *testing.T) {
	_, router, _, token := fixture(t)
	require.Equal(t, 202, request(router, "POST", "/v1/videos", token.Key, validBody).Code)
	replay := strings.Replace(validBody, `"duration":1`, `"mode":"text","duration":1`, 1)
	response := request(router, "POST", "/v1/videos", token.Key, replay)
	assert.Equal(t, 200, response.Code, response.Body.String())
	var data map[string]interface{}
	require.NoError(t, common.Unmarshal(response.Body.Bytes(), &data))
	assert.Equal(t, "test-one", data["request_id"])
}

func TestPublicImageDefiniteFirstSubmissionRejectionRefundsButUncertainAttemptDoesNot(t *testing.T) {
	for _, uncertainFirst := range []bool{false, true} {
		t.Run(map[bool]string{false: "first_definite_refusal", true: "previous_uncertain_attempt"}[uncertainFirst], func(t *testing.T) {
			s, _, user, token := fixture(t)
			body := `{"provider_id":"video-aixingtu-api","request_id":"public-image-expiry","model":"grok-imagine-video-official","prompt":"A ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
			row, _, err := model.ReservePublicVideo(model.PublicVideoTask{ID: "vjob_22222222222222222222222222222222", UserID: user.Id, TokenID: token.Id,
				ClientRequestID: "image-expiry", Fingerprint: strings.Repeat("a", 64), Model: "grok-imagine-video-official", Group: "default",
				Body: body, ReservedCNY: "0.450000", QuotaPerCNY: "500000"})
			require.NoError(t, err)
			posts := 0
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "GET" {
					w.WriteHeader(404)
					_, _ = w.Write([]byte(`{}`))
					return
				}
				posts++
				if uncertainFirst && posts == 1 {
					_, _ = w.Write([]byte(`{"invalid_json":`))
					return
				}
				w.WriteHeader(400)
				_, _ = w.Write([]byte(`{"task_created":false,"upstream_submitted":false,"billing_contract_version":"xtai-video-billing-v2.2","request_id":"public-image-expiry","error":{"code":"video_image_probe_failed","phase":"validate"}}`))
			}))
			t.Cleanup(backend.Close)
			s.backend = backend.URL
			if uncertainFirst {
				require.Error(t, s.process(row))
				require.NoError(t, model.DB.First(&row, "id = ?", row.ID).Error)
			}
			require.NoError(t, s.process(row))
			require.NoError(t, model.DB.First(&row, "id = ?", row.ID).Error)
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			if uncertainFirst {
				assert.Equal(t, "pending_review", row.State)
				assert.Empty(t, row.ChargedCNY)
				assert.Equal(t, 775000, user.Quota)
			} else {
				assert.Equal(t, "settled", row.State)
				assert.Equal(t, "0.000000", row.ChargedCNY)
				assert.Equal(t, 1000000, user.Quota)
			}
		})
	}
}

func TestPublicImageRefundRechecksCurrentAttemptCounterAndSettledRowNeverPosts(t *testing.T) {
	s, _, user, token := fixture(t)
	body := `{"provider_id":"video-aixingtu-api","request_id":"public-concurrent-image","model":"grok-imagine-video-official","prompt":"A ball","duration":1,"resolution":"480p","mode":"reference","images":["https://media.example/a.png"],"image_roles":["reference"],"image_identities":["` + strings.Repeat("a", 64) + `"]}`
	row, _, err := model.ReservePublicVideo(model.PublicVideoTask{ID: "vjob_33333333333333333333333333333333", UserID: user.Id, TokenID: token.Id,
		ClientRequestID: "concurrent-image", Fingerprint: strings.Repeat("a", 64), Model: "grok-imagine-video-official", Group: "default",
		Body: body, ReservedCNY: "0.450000", QuotaPerCNY: "500000"})
	require.NoError(t, err)
	posts := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{}`))
			return
		}
		posts++
		// Deterministically interleave another worker after the caller loaded its counter.
		require.NoError(t, model.DB.Model(&row).Update("submit_attempts", 2).Error)
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"task_created":false,"upstream_submitted":false,"billing_contract_version":"xtai-video-billing-v2.2","request_id":"public-concurrent-image","error":{"code":"video_image_probe_failed","phase":"validate"}}`))
	}))
	t.Cleanup(backend.Close)
	s.backend = backend.URL
	require.Error(t, s.process(row))
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	assert.Equal(t, 775000, user.Quota)
	require.NoError(t, model.DB.Model(&row).Updates(map[string]interface{}{"state": "settled", "charged_cny": "0.450000"}).Error)
	require.NoError(t, s.process(row))
	assert.Equal(t, 1, posts)
}
