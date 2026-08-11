package service

import (
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func walletRelayInfo(userID, tokenID int, tokenKey string, unlimited bool) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		UserId:          userID,
		TokenId:         tokenID,
		TokenKey:        tokenKey,
		TokenUnlimited:  unlimited,
		ForcePreConsume: true,
		HardQuota:       true,
		RequestId:       "request-overdraft",
		OriginModelName: "seedance-2.0",
		UserSetting: dto.UserSetting{
			BillingPreference: "wallet_only",
		},
	}
}

func TestVideoWalletReservationRequiresFullHardQuota(t *testing.T) {
	truncate(t)
	gin.SetMode(gin.TestMode)
	const userID, tokenID = 101, 101
	seedUser(t, userID, 100)
	seedToken(t, tokenID, userID, "sk-overdraft", 10_000)
	require.NoError(t, model.DB.Model(&model.Token{}).
		Where("id = ?", tokenID).Update("unlimited_quota", true).Error)

	session, apiErr := NewBillingSession(
		&gin.Context{},
		walletRelayInfo(userID, tokenID, "sk-overdraft", true),
		150,
	)

	assert.Nil(t, session)
	require.NotNil(t, apiErr)
	assert.Equal(t, types.ErrorCodeInsufficientUserQuota, apiErr.GetErrorCode())
	assert.Equal(t, 100, getUserQuota(t, userID))

	second, apiErr := NewBillingSession(
		&gin.Context{},
		walletRelayInfo(userID, tokenID, "sk-overdraft", true),
		100,
	)
	require.Nil(t, apiErr)
	require.NotNil(t, second)
	assert.Equal(t, 0, getUserQuota(t, userID))

	require.NoError(t, model.IncreaseUserQuota(userID, 100, true))
	third, apiErr := NewBillingSession(
		&gin.Context{},
		walletRelayInfo(userID, tokenID, "sk-overdraft", true),
		100,
	)
	require.Nil(t, apiErr)
	require.NotNil(t, third)
	assert.Equal(t, 0, getUserQuota(t, userID))
}

func TestConcurrentVideoWalletReservationsNeverCrossZero(t *testing.T) {
	truncate(t)
	gin.SetMode(gin.TestMode)
	const userID = 102
	seedUser(t, userID, 100)
	for tokenID, key := range map[int]string{102: "sk-overdraft-a", 103: "sk-overdraft-b"} {
		seedToken(t, tokenID, userID, key, 10_000)
		require.NoError(t, model.DB.Model(&model.Token{}).
			Where("id = ?", tokenID).Update("unlimited_quota", true).Error)
	}

	var wg sync.WaitGroup
	results := make(chan bool, 2)
	for tokenID, key := range map[int]string{102: "sk-overdraft-a", 103: "sk-overdraft-b"} {
		wg.Add(1)
		go func(tokenID int, key string) {
			defer wg.Done()
			_, apiErr := NewBillingSession(
				&gin.Context{},
				walletRelayInfo(userID, tokenID, key, true),
				60,
			)
			results <- apiErr == nil
		}(tokenID, key)
	}
	wg.Wait()
	close(results)
	successes := 0
	for success := range results {
		if success {
			successes++
		}
	}

	assert.Equal(t, 1, successes)
	assert.Equal(t, 40, getUserQuota(t, userID))
}

func TestHardLimitedTokenNeverOverdraftsForWalletReservation(t *testing.T) {
	truncate(t)
	gin.SetMode(gin.TestMode)
	const userID, tokenID = 104, 104
	seedUser(t, userID, 100)
	seedToken(t, tokenID, userID, "sk-hard-limit", 50)

	session, apiErr := NewBillingSession(
		&gin.Context{},
		walletRelayInfo(userID, tokenID, "sk-hard-limit", false),
		150,
	)

	assert.Nil(t, session)
	require.NotNil(t, apiErr)
	assert.Equal(t, types.ErrorCodePreConsumeTokenQuotaFailed, apiErr.GetErrorCode())
	assert.Equal(t, 100, getUserQuota(t, userID))
	assert.Equal(t, 50, getTokenRemainQuota(t, tokenID))
}

func TestHardLimitedTokenNeverOverdraftsForSettlementSupplement(t *testing.T) {
	truncate(t)
	gin.SetMode(gin.TestMode)
	const userID, tokenID = 105, 105
	seedUser(t, userID, 100)
	seedToken(t, tokenID, userID, "sk-hard-settlement", 50)

	session, apiErr := NewBillingSession(
		&gin.Context{},
		walletRelayInfo(userID, tokenID, "sk-hard-settlement", false),
		40,
	)
	require.Nil(t, apiErr)
	require.NotNil(t, session)
	assert.Equal(t, 60, getUserQuota(t, userID))
	assert.Equal(t, 10, getTokenRemainQuota(t, tokenID))

	err := session.Settle(60)
	require.ErrorIs(t, err, model.ErrTokenQuotaInsufficient)
	assert.Equal(t, 60, getUserQuota(t, userID))
	assert.Equal(t, 10, getTokenRemainQuota(t, tokenID))

	require.NoError(t, session.Settle(45))
	assert.Equal(t, 55, getUserQuota(t, userID))
	assert.Equal(t, 5, getTokenRemainQuota(t, tokenID))
}
