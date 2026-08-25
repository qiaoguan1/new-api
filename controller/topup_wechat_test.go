package controller

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/wechatpay-apiv3/wechatpay-go/core"
	"github.com/wechatpay-apiv3/wechatpay-go/services/payments"
	"github.com/wechatpay-apiv3/wechatpay-go/services/payments/native"
)

type fakeWechatOrderQuerier struct {
	calls []string
	query func(context.Context, native.QueryOrderByOutTradeNoRequest) (*payments.Transaction, error)
}

func (f *fakeWechatOrderQuerier) QueryOrderByOutTradeNo(
	ctx context.Context,
	req native.QueryOrderByOutTradeNoRequest,
) (*payments.Transaction, *core.APIResult, error) {
	tradeNo := ""
	if req.OutTradeNo != nil {
		tradeNo = *req.OutTradeNo
	}
	f.calls = append(f.calls, tradeNo)
	transaction, err := f.query(ctx, req)
	return transaction, nil, err
}

type fakeWechatOrderSyncStore struct {
	credited  []string
	failed    []string
	creditErr error
	failErr   error
}

func (f *fakeWechatOrderSyncStore) credit(
	tradeNo string,
	transactionID string,
	clientIP string,
) (*model.TopUp, error) {
	f.credited = append(f.credited, tradeNo)
	if f.creditErr != nil {
		return nil, f.creditErr
	}
	return &model.TopUp{
		TradeNo:         tradeNo,
		PaymentMethod:   model.PaymentMethodWechatPay,
		PaymentProvider: model.PaymentProviderWechatPay,
		Status:          common.TopUpStatusSuccess,
	}, nil
}

func (f *fakeWechatOrderSyncStore) fail(tradeNo string) (*model.TopUp, error) {
	f.failed = append(f.failed, tradeNo)
	if f.failErr != nil {
		return nil, f.failErr
	}
	return &model.TopUp{
		TradeNo:         tradeNo,
		PaymentMethod:   model.PaymentMethodWechatPay,
		PaymentProvider: model.PaymentProviderWechatPay,
		Status:          common.TopUpStatusFailed,
	}, nil
}

func pendingWechatTopUp(userID int, tradeNo string) *model.TopUp {
	return &model.TopUp{
		UserId:          userID,
		Amount:          20,
		Money:           20,
		TradeNo:         tradeNo,
		PaymentMethod:   model.PaymentMethodWechatPay,
		PaymentProvider: model.PaymentProviderWechatPay,
		CreateTime:      common.GetTimestamp() - int64(wechatPayOrderTTL/time.Second) - 1,
		Status:          common.TopUpStatusPending,
	}
}

func wechatTransaction(
	cfg setting.WechatPayConfig,
	tradeNo string,
	state string,
	amountCents int64,
) *payments.Transaction {
	currency := "CNY"
	return &payments.Transaction{
		Appid:         ptr(cfg.AppID),
		Mchid:         ptr(cfg.MchID),
		OutTradeNo:    ptr(tradeNo),
		TransactionId: ptr("TX-" + tradeNo),
		TradeState:    ptr(state),
		Amount: &payments.TransactionAmount{
			Total:    ptr(amountCents),
			Currency: ptr(currency),
		},
	}
}

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

func TestReconcileWechatPendingTopUpsFiltersCurrentUserAndCapsQueries(t *testing.T) {
	cfg := setting.WechatPayConfig{MchID: "1900000109", AppID: "wx1234567890abcd"}
	topups := []*model.TopUp{}
	for i := 0; i < 6; i++ {
		topups = append(topups, pendingWechatTopUp(7, fmt.Sprintf("WX-CURRENT-%d", i)))
	}
	topups = append(topups,
		pendingWechatTopUp(8, "WX-OTHER-USER"),
		&model.TopUp{UserId: 7, TradeNo: "EPAY", Status: common.TopUpStatusPending},
		&model.TopUp{
			UserId: 7, TradeNo: "WX-DONE", Status: common.TopUpStatusSuccess,
			PaymentMethod: model.PaymentMethodWechatPay, PaymentProvider: model.PaymentProviderWechatPay,
		},
		&model.TopUp{
			UserId: 7, TradeNo: "WX-RECENT", Status: common.TopUpStatusPending,
			PaymentMethod: model.PaymentMethodWechatPay, PaymentProvider: model.PaymentProviderWechatPay,
			CreateTime: common.GetTimestamp(),
		},
		&model.TopUp{
			UserId: 7, TradeNo: "WX/INVALID", Status: common.TopUpStatusPending,
			PaymentMethod: model.PaymentMethodWechatPay, PaymentProvider: model.PaymentProviderWechatPay,
			CreateTime: common.GetTimestamp() - 600,
		},
	)
	querier := &fakeWechatOrderQuerier{query: func(
		ctx context.Context,
		req native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, error) {
		return wechatTransaction(cfg, *req.OutTradeNo, "NOTPAY", 2000), nil
	}}
	store := &fakeWechatOrderSyncStore{}

	summary := reconcileWechatPendingTopUpsWithBudget(
		context.Background(), 7, topups, cfg, querier, store, "127.0.0.1",
		wechatOrderSyncBudget{Limit: 100, Timeout: time.Hour},
	)

	require.Equal(t, []string{
		"WX-CURRENT-0", "WX-CURRENT-1", "WX-CURRENT-2", "WX-CURRENT-3", "WX-CURRENT-4",
	}, querier.calls)
	require.Empty(t, store.credited)
	require.Empty(t, store.failed)
	require.Equal(t, wechatOrderSyncSummary{Queried: 5}, summary)
}

func TestReconcileWechatPendingTopUpsAppliesOnlyTrustedTerminalStates(t *testing.T) {
	cfg := setting.WechatPayConfig{MchID: "1900000109", AppID: "wx1234567890abcd"}
	success := pendingWechatTopUp(7, "WX-SUCCESS")
	closed := pendingWechatTopUp(7, "WX-CLOSED")
	userPaying := pendingWechatTopUp(7, "WX-USERPAYING")
	invalidSuccess := pendingWechatTopUp(7, "WX-INVALID-SUCCESS")
	transactions := map[string]*payments.Transaction{
		"WX-SUCCESS":         wechatTransaction(cfg, "WX-SUCCESS", "SUCCESS", 2000),
		"WX-CLOSED":          wechatTransaction(cfg, "WX-CLOSED", "CLOSED", 2000),
		"WX-USERPAYING":      wechatTransaction(cfg, "WX-USERPAYING", "USERPAYING", 2000),
		"WX-INVALID-SUCCESS": wechatTransaction(cfg, "WX-INVALID-SUCCESS", "SUCCESS", 1999),
	}
	querier := &fakeWechatOrderQuerier{query: func(
		ctx context.Context,
		req native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, error) {
		return transactions[*req.OutTradeNo], nil
	}}
	store := &fakeWechatOrderSyncStore{}

	reconcileWechatPendingTopUpsWithBudget(
		context.Background(), 7,
		[]*model.TopUp{success, closed, userPaying, invalidSuccess},
		cfg, querier, store, "127.0.0.1",
		wechatOrderSyncBudget{Limit: 5, Timeout: time.Second},
	)

	require.Equal(t, []string{"WX-SUCCESS"}, store.credited)
	require.Equal(t, []string{"WX-CLOSED"}, store.failed)
	require.Equal(t, common.TopUpStatusSuccess, success.Status)
	require.Equal(t, common.TopUpStatusFailed, closed.Status)
	require.Equal(t, common.TopUpStatusPending, userPaying.Status)
	require.Equal(t, common.TopUpStatusPending, invalidSuccess.Status)
}

func TestReconcileWechatPendingTopUpsFailsOpenOnErrorsAndSharedDeadline(t *testing.T) {
	cfg := setting.WechatPayConfig{MchID: "1900000109", AppID: "wx1234567890abcd"}
	first := pendingWechatTopUp(7, "WX-BLOCKED")
	second := pendingWechatTopUp(7, "WX-NOT-QUERIED")
	querier := &fakeWechatOrderQuerier{query: func(
		ctx context.Context,
		req native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}}
	store := &fakeWechatOrderSyncStore{}
	started := time.Now()

	reconcileWechatPendingTopUpsWithBudget(
		context.Background(), 7, []*model.TopUp{first, second}, cfg, querier, store,
		"127.0.0.1", wechatOrderSyncBudget{Limit: 5, Timeout: 20 * time.Millisecond},
	)

	require.Less(t, time.Since(started), 250*time.Millisecond)
	require.Equal(t, []string{"WX-BLOCKED"}, querier.calls)
	require.Empty(t, store.credited)
	require.Empty(t, store.failed)
	require.Equal(t, common.TopUpStatusPending, first.Status)
	require.Equal(t, common.TopUpStatusPending, second.Status)

	errorQuerier := &fakeWechatOrderQuerier{query: func(
		ctx context.Context,
		req native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, error) {
		return nil, errors.New("upstream unavailable")
	}}
	reconcileWechatPendingTopUpsWithBudget(
		context.Background(), 7, []*model.TopUp{first}, cfg, errorQuerier, store,
		"127.0.0.1", wechatOrderSyncBudget{Limit: 5, Timeout: time.Second},
	)
	require.Equal(t, common.TopUpStatusPending, first.Status)

	success := pendingWechatTopUp(7, "WX-STORE-CREDIT-ERROR")
	closed := pendingWechatTopUp(7, "WX-STORE-FAIL-ERROR")
	storeErrors := &fakeWechatOrderSyncStore{
		creditErr: errors.New("credit unavailable"),
		failErr:   errors.New("status update unavailable"),
	}
	storeErrorQuerier := &fakeWechatOrderQuerier{query: func(
		ctx context.Context,
		req native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, error) {
		if *req.OutTradeNo == success.TradeNo {
			return wechatTransaction(cfg, success.TradeNo, "SUCCESS", 2000), nil
		}
		return wechatTransaction(cfg, closed.TradeNo, "CLOSED", 2000), nil
	}}
	reconcileWechatPendingTopUpsWithBudget(
		context.Background(), 7, []*model.TopUp{success, closed}, cfg,
		storeErrorQuerier, storeErrors, "127.0.0.1",
		wechatOrderSyncBudget{Limit: 5, Timeout: time.Second},
	)
	require.Equal(t, common.TopUpStatusPending, success.Status)
	require.Equal(t, common.TopUpStatusPending, closed.Status)
}
