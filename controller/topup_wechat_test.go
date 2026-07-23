package controller

import (
	"io"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/wechatpay-apiv3/wechatpay-go/services/payments"
)

func TestLimitWechatPayNotifyBodyRejectsOversizedPayload(t *testing.T) {
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(
		"POST",
		"/api/wechatpay/notify",
		strings.NewReader(strings.Repeat("a", int(wechatPayNotifyMaxBodyBytes)+1)),
	)

	limitWechatPayNotifyBody(c)
	_, err := io.ReadAll(c.Request.Body)
	require.Error(t, err)
}

func TestMoneyToCentsRoundsToCurrencyPrecision(t *testing.T) {
	cents, err := moneyToCents(12.345)
	require.NoError(t, err)
	require.EqualValues(t, 1235, cents)

	_, err = moneyToCents(0.004)
	require.Error(t, err)
}

func TestValidateWechatTransaction(t *testing.T) {
	cfg := setting.WechatPayConfig{MchID: "1900000109", AppID: "wx1234567890abcd"}
	topUp := &model.TopUp{
		TradeNo:         "WX-ORDER-1",
		Money:           12.34,
		PaymentMethod:   model.PaymentMethodWechatPay,
		PaymentProvider: model.PaymentProviderWechatPay,
	}
	transaction := &payments.Transaction{
		Appid:      ptr(cfg.AppID),
		Mchid:      ptr(cfg.MchID),
		OutTradeNo: ptr(topUp.TradeNo),
		TradeState: ptr("SUCCESS"),
		Amount: &payments.TransactionAmount{
			Total:         ptr(int64(1234)),
			PayerTotal:    ptr(int64(1234)),
			Currency:      ptr("CNY"),
			PayerCurrency: ptr("CNY"),
		},
	}

	require.NoError(t, validateWechatTransaction(transaction, topUp, cfg))

	transaction.Amount.Total = ptr(int64(1235))
	require.ErrorContains(t, validateWechatTransaction(transaction, topUp, cfg), "amount_mismatch")

	transaction.Amount.Total = ptr(int64(1234))
	topUp.PaymentProvider = model.PaymentProviderStripe
	require.ErrorContains(t, validateWechatTransaction(transaction, topUp, cfg), "payment_provider_or_method_mismatch")
}
