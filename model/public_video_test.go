package model

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"strings"
	"testing"
)

func publicVideoFixture(t *testing.T) (User, Token, PublicVideoTask) {
	t.Helper()
	t.Setenv("QUOTA_DB_AUTHORITATIVE", "true")
	require.NoError(t, DB.AutoMigrate(&PublicVideoTask{}))
	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")
	u := User{Username: "pv" + suffix[:16], AffCode: suffix, Password: "not-a-real-password", Status: common.UserStatusEnabled, Quota: 2000000, Group: "default"}
	require.NoError(t, DB.Create(&u).Error)
	token := Token{UserId: u.Id, Key: suffix, Name: "public-video-test", Status: common.TokenStatusEnabled, ExpiredTime: -1, RemainQuota: 2000000}
	require.NoError(t, DB.Create(&token).Error)
	row := PublicVideoTask{ID: "vjob_" + suffix, UserID: u.Id, TokenID: token.Id, ClientRequestID: "same", Fingerprint: "fp", Model: "grok-imagine-video-official", ReservedCNY: "0.675000", QuotaPerCNY: "500000", Group: "default"}
	return u, token, row
}
func TestPublicVideoExactReserveReplayAndSettle(t *testing.T) {
	u, token, row := publicVideoFixture(t)
	created, reused, e := ReservePublicVideo(row)
	require.NoError(t, e)
	assert.False(t, reused)
	assert.Equal(t, 337500, created.ReservedQuota)
	_, reused, e = ReservePublicVideo(row)
	require.NoError(t, e)
	assert.True(t, reused)
	changed := row
	changed.Fingerprint = "different"
	_, _, e = ReservePublicVideo(changed)
	assert.ErrorIs(t, e, ErrPublicVideoConflict)
	require.NoError(t, SettlePublicVideo(row.ID, "0.450000", `{"status":"succeeded"}`))
	require.NoError(t, SettlePublicVideo(row.ID, "0.450000", `{"status":"succeeded"}`))
	assert.ErrorIs(t, SettlePublicVideo(row.ID, "0.500000", `{}`), ErrPublicVideoConflict)
	require.NoError(t, DB.First(&u, u.Id).Error)
	require.NoError(t, DB.First(&token, token.Id).Error)
	assert.Equal(t, 1775000, u.Quota)
	assert.Equal(t, 225000, u.UsedQuota)
	assert.Equal(t, 1, u.RequestCount)
	assert.Equal(t, 1775000, token.RemainQuota)
	assert.Equal(t, 225000, token.UsedQuota)
	var count int64
	require.NoError(t, DB.Model(&Log{}).Where("request_id = ?", row.ID).Count(&count).Error)
	assert.EqualValues(t, 1, count)
}
func TestPublicVideoInsufficientAndForbiddenNeverReserve(t *testing.T) {
	u, token, row := publicVideoFixture(t)
	require.NoError(t, DB.Model(&u).Update("quota", 1).Error)
	_, _, e := ReservePublicVideo(row)
	assert.ErrorIs(t, e, ErrPublicVideoQuota)
	require.NoError(t, DB.Model(&u).Update("quota", 2000000).Error)
	require.NoError(t, DB.Model(&token).Updates(map[string]interface{}{"model_limits_enabled": true, "model_limits": "other-model"}).Error)
	_, _, e = ReservePublicVideo(row)
	assert.ErrorIs(t, e, ErrPublicVideoForbidden)
	var count int64
	require.NoError(t, DB.Model(&PublicVideoTask{}).Where("id = ?", row.ID).Count(&count).Error)
	assert.Zero(t, count)
}
func TestPublicVideoFullRefundAndSupplement(t *testing.T) {
	for _, amount := range []string{"0.000000", "0.900000"} {
		u, token, row := publicVideoFixture(t)
		_, _, e := ReservePublicVideo(row)
		require.NoError(t, e)
		require.NoError(t, SettlePublicVideo(row.ID, amount, `{"status":"failed"}`))
		quota, e := PublicVideoQuota(amount, "500000")
		require.NoError(t, e)
		require.NoError(t, DB.First(&u, u.Id).Error)
		require.NoError(t, DB.First(&token, token.Id).Error)
		assert.Equal(t, 2000000-quota, u.Quota)
		assert.Equal(t, 2000000-quota, token.RemainQuota)
	}
}

func TestPublicVideoSupplementNeverOverdrawsUserOrLimitedToken(t *testing.T) {
	for _, test := range []struct {
		name                  string
		userQuota, tokenQuota int
		unlimited, funded     bool
	}{
		{"user_short", 100000, 200000, false, false},
		{"limited_token_short", 200000, 100000, false, false},
		{"unlimited_still_needs_user_funds", 100000, 0, true, false},
		{"funded_limited", 200000, 200000, false, true},
		{"funded_unlimited", 200000, 0, true, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			u, token, candidate := publicVideoFixture(t)
			candidate.Fingerprint = strings.Repeat("a", 64)
			envelope, err := common.Marshal(map[string]interface{}{"_public_reservation": map[string]interface{}{"schema_version": "xtai-public-video-estimated-reservation-v1", "price_source": "nodyhub_operator_testing_estimate", "pricing_kind": "estimated_reservation", "verification_status": "unverified", "is_upper_bound": false, "reserved_cny_exact": candidate.ReservedCNY, "payload_fingerprint": candidate.Fingerprint}})
			require.NoError(t, err)
			candidate.Body = string(envelope)
			row, _, err := ReservePublicVideo(candidate)
			require.NoError(t, err)
			require.NoError(t, DB.Model(&u).Update("quota", test.userQuota).Error)
			require.NoError(t, DB.Model(&token).Updates(map[string]interface{}{"remain_quota": test.tokenQuota, "unlimited_quota": test.unlimited}).Error)
			err = SettlePublicVideo(row.ID, "0.900000", `{"status":"succeeded","billing":{"charged_amount":"0.900000"}}`)
			require.NoError(t, DB.First(&row, "id = ?", row.ID).Error)
			require.NoError(t, DB.First(&u, u.Id).Error)
			require.NoError(t, DB.First(&token, token.Id).Error)
			var logs int64
			require.NoError(t, DB.Model(&Log{}).Where("request_id = ?", row.ID).Count(&logs).Error)
			if test.funded {
				require.NoError(t, err)
				assert.Equal(t, test.userQuota-112500, u.Quota)
				assert.Equal(t, test.tokenQuota-112500, token.RemainQuota)
				assert.Equal(t, "settled", row.State)
				assert.EqualValues(t, 1, logs)
			} else {
				require.ErrorIs(t, err, ErrPublicVideoQuota)
				assert.Equal(t, test.userQuota, u.Quota)
				assert.Equal(t, test.tokenQuota, token.RemainQuota)
				assert.Equal(t, "reserved", row.State)
				assert.Empty(t, row.ChargedCNY)
				assert.Empty(t, row.Snapshot)
				assert.Zero(t, logs)
			}
		})
	}
}

func TestPublicVideoMalformedEstimateEnvelopeCannotUseLegacyDebtFallback(t *testing.T) {
	u, token, candidate := publicVideoFixture(t)
	candidate.Body = `{"_public_reservation":{"schema_version":"wrong","price_source":"nodyhub_operator_testing_estimate","pricing_kind":"estimated_reservation"}}`
	row, _, err := ReservePublicVideo(candidate)
	require.NoError(t, err)
	require.NoError(t, DB.Model(&u).Update("quota", 0).Error)
	require.NoError(t, DB.Model(&token).Update("remain_quota", 0).Error)
	assert.Error(t, SettlePublicVideo(row.ID, "0.900000", `{"status":"succeeded"}`))
	require.NoError(t, DB.First(&u, u.Id).Error)
	require.NoError(t, DB.First(&row, "id = ?", row.ID).Error)
	assert.Zero(t, u.Quota)
	assert.Equal(t, "reserved", row.State)
	assert.Empty(t, row.ChargedCNY)
}
func TestPublicVideoQuotaBoundaries(t *testing.T) {
	q, e := PublicVideoQuota("0.675000", "500000")
	require.NoError(t, e)
	assert.Equal(t, 337500, q)
	for _, amount := range []string{"-1", "NaN", "1e999999", "100001"} {
		_, e = PublicVideoQuota(amount, "500000")
		assert.Error(t, e)
	}
}

func TestPublicVideoTokenModeChangePreservesQuotaLedger(t *testing.T) {
	for _, initialUnlimited := range []bool{true, false} {
		_, token, row := publicVideoFixture(t)
		require.NoError(t, DB.Model(&token).Update("unlimited_quota", initialUnlimited).Error)
		_, _, e := ReservePublicVideo(row)
		require.NoError(t, e)
		require.NoError(t, DB.Model(&token).Update("unlimited_quota", !initialUnlimited).Error)
		require.NoError(t, SettlePublicVideo(row.ID, "0.450000", `{"status":"succeeded"}`))
		require.NoError(t, DB.First(&token, token.Id).Error)
		assert.Equal(t, 1775000, token.RemainQuota)
		assert.Equal(t, 225000, token.UsedQuota)
	}
}
