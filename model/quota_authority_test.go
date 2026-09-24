package model

import (
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"testing"
)

func TestQuotaAuthorityNativeAndVideoShareReservation(t *testing.T) {
	u, token, row := publicVideoFixture(t)
	_, _, e := ReservePublicVideo(row)
	require.NoError(t, e)
	assert.Error(t, ReserveUserQuota(u.Id, 1800000))
	assert.Error(t, ReserveTokenQuota(token.Id, 1800000))
	require.NoError(t, ReserveUserQuota(u.Id, 1000000))
	require.NoError(t, ReserveTokenQuota(token.Id, 1000000))
	q, e := GetUserQuota(u.Id, false)
	require.NoError(t, e)
	assert.Equal(t, 662500, q)
	require.NoError(t, SettlePublicVideo(row.ID, "0.450000", `{"status":"succeeded"}`))
	q, e = GetUserQuota(u.Id, false)
	require.NoError(t, e)
	assert.Equal(t, 775000, q)
	tk, e := GetTokenByKey(token.Key, false)
	require.NoError(t, e)
	assert.Equal(t, 775000, tk.RemainQuota)
}

func TestQuotaAuthorityFinalSupplementCanRepresentDebt(t *testing.T) {
	u, token, row := publicVideoFixture(t)
	require.NoError(t, DB.Model(&u).Update("quota", 337500).Error)
	require.NoError(t, DB.Model(&token).Update("remain_quota", 337500).Error)
	_, _, e := ReservePublicVideo(row)
	require.NoError(t, e)
	require.NoError(t, SettlePublicVideo(row.ID, "0.900000", `{"status":"succeeded"}`))
	q, e := GetUserQuota(u.Id, false)
	require.NoError(t, e)
	assert.Equal(t, -112500, q)
	assert.Error(t, ReserveUserQuota(u.Id, 1))
}

func TestQuotaAuthorityLargeHistoricalBalances(t *testing.T) {
	u, token, row := publicVideoFixture(t)
	const large = 5000000000000
	require.NoError(t, DB.Model(&u).Updates(map[string]interface{}{"quota": large, "used_quota": large}).Error)
	require.NoError(t, DB.Model(&token).Updates(map[string]interface{}{"remain_quota": large, "used_quota": large}).Error)
	require.NoError(t, ReserveTokenQuota(token.Id, 100))
	require.NoError(t, ReserveUserQuota(u.Id, 100))
	_, _, e := ReservePublicVideo(row)
	require.NoError(t, e)
	require.NoError(t, SettlePublicVideo(row.ID, "0.450000", `{"status":"succeeded"}`))
	quota, e := GetUserQuota(u.Id, false)
	require.NoError(t, e)
	assert.Equal(t, large-225100, quota)
}
