package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

func TestRechargeWechatPayIsIdempotent(t *testing.T) {
	truncateTables(t)
	user := User{Username: "wxpay-user", Password: "password123", Quota: 100, Group: "default"}
	require.NoError(t, DB.Create(&user).Error)
	topUp := TopUp{
		UserId:          user.Id,
		Amount:          2,
		Money:           2,
		TradeNo:         "WX-IDEMPOTENT-1",
		PaymentMethod:   PaymentMethodWechatPay,
		PaymentProvider: PaymentProviderWechatPay,
		Status:          common.TopUpStatusPending,
	}
	require.NoError(t, DB.Create(&topUp).Error)

	require.NoError(t, RechargeWechatPay(topUp.TradeNo, "4200000001", "127.0.0.1"))
	require.NoError(t, RechargeWechatPay(topUp.TradeNo, "4200000001", "127.0.0.1"))

	var reloadedUser User
	require.NoError(t, DB.First(&reloadedUser, user.Id).Error)
	require.Equal(t, 100+int(2*common.QuotaPerUnit), reloadedUser.Quota)

	var reloadedTopUp TopUp
	require.NoError(t, DB.First(&reloadedTopUp, topUp.Id).Error)
	require.Equal(t, common.TopUpStatusSuccess, reloadedTopUp.Status)
}

func TestRechargeWechatPayRejectsProviderMismatch(t *testing.T) {
	truncateTables(t)
	user := User{Username: "wxpay-mismatch", Password: "password123", Quota: 100, Group: "default"}
	require.NoError(t, DB.Create(&user).Error)
	topUp := TopUp{
		UserId:          user.Id,
		Amount:          2,
		Money:           2,
		TradeNo:         "WX-MISMATCH-1",
		PaymentMethod:   PaymentMethodWechatPay,
		PaymentProvider: PaymentProviderStripe,
		Status:          common.TopUpStatusPending,
	}
	require.NoError(t, DB.Create(&topUp).Error)

	require.ErrorIs(t, RechargeWechatPay(topUp.TradeNo, "4200000002", "127.0.0.1"), ErrPaymentMethodMismatch)

	var reloadedUser User
	require.NoError(t, DB.First(&reloadedUser, user.Id).Error)
	require.Equal(t, 100, reloadedUser.Quota)
}
