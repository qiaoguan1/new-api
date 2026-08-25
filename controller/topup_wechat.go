package controller

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"
	"github.com/wechatpay-apiv3/wechatpay-go/core"
	"github.com/wechatpay-apiv3/wechatpay-go/core/auth/verifiers"
	"github.com/wechatpay-apiv3/wechatpay-go/core/notify"
	"github.com/wechatpay-apiv3/wechatpay-go/core/option"
	"github.com/wechatpay-apiv3/wechatpay-go/services/payments"
	"github.com/wechatpay-apiv3/wechatpay-go/services/payments/native"
	"github.com/wechatpay-apiv3/wechatpay-go/utils"
)

const (
	wechatPayOrderTTL           = 5 * time.Minute
	wechatPayNotifyMaxBodyBytes = int64(1 << 20)
	wechatPayListSyncLimit      = 5
	wechatPayListSyncTimeout    = 5 * time.Second
)

var (
	wechatPayClientMu     sync.Mutex
	wechatPayCachedClient *core.Client
	wechatPayCachedConfig setting.WechatPayConfig
)

type wechatPayRequest struct {
	Amount int64 `json:"amount"`
}

type wechatPayValidationError string

func (e wechatPayValidationError) Error() string { return string(e) }

type wechatOrderQuerier interface {
	QueryOrderByOutTradeNo(
		context.Context,
		native.QueryOrderByOutTradeNoRequest,
	) (*payments.Transaction, *core.APIResult, error)
}

type wechatOrderSyncStore interface {
	credit(tradeNo string, transactionID string, clientIP string) (*model.TopUp, error)
	fail(tradeNo string) (*model.TopUp, error)
}

type modelWechatOrderSyncStore struct{}

func (modelWechatOrderSyncStore) credit(
	tradeNo string,
	transactionID string,
	clientIP string,
) (*model.TopUp, error) {
	if err := model.RechargeWechatPay(tradeNo, transactionID, clientIP); err != nil {
		if errors.Is(err, model.ErrTopUpStatusInvalid) {
			return model.GetTopUpByTradeNo(tradeNo), nil
		}
		return nil, err
	}
	return model.GetTopUpByTradeNo(tradeNo), nil
}

func (modelWechatOrderSyncStore) fail(tradeNo string) (*model.TopUp, error) {
	if err := model.UpdatePendingTopUpStatus(
		tradeNo,
		model.PaymentProviderWechatPay,
		common.TopUpStatusFailed,
	); err != nil {
		if errors.Is(err, model.ErrTopUpStatusInvalid) {
			return model.GetTopUpByTradeNo(tradeNo), nil
		}
		return nil, err
	}
	return model.GetTopUpByTradeNo(tradeNo), nil
}

type wechatOrderSyncBudget struct {
	Limit   int
	Timeout time.Duration
}

func eligiblePendingWechatTopUp(topUp *model.TopUp, userID int) bool {
	return topUp != nil &&
		topUp.UserId == userID &&
		topUp.Status == common.TopUpStatusPending &&
		topUp.PaymentMethod == model.PaymentMethodWechatPay &&
		topUp.PaymentProvider == model.PaymentProviderWechatPay
}

func reconcileWechatPendingTopUpsWithBudget(
	ctx context.Context,
	userID int,
	topups []*model.TopUp,
	cfg setting.WechatPayConfig,
	querier wechatOrderQuerier,
	store wechatOrderSyncStore,
	clientIP string,
	budget wechatOrderSyncBudget,
) {
	if ctx == nil || querier == nil || store == nil || budget.Limit <= 0 || budget.Timeout <= 0 {
		return
	}
	syncCtx, cancel := context.WithTimeout(ctx, budget.Timeout)
	defer cancel()
	queried := 0
	for _, topUp := range topups {
		if queried >= budget.Limit || syncCtx.Err() != nil {
			break
		}
		if !eligiblePendingWechatTopUp(topUp, userID) {
			continue
		}
		queried++
		transaction, _, err := querier.QueryOrderByOutTradeNo(
			syncCtx,
			native.QueryOrderByOutTradeNoRequest{
				OutTradeNo: ptr(topUp.TradeNo),
				Mchid:      ptr(cfg.MchID),
			},
		)
		if err != nil || transaction == nil || transaction.TradeState == nil {
			continue
		}

		var refreshed *model.TopUp
		switch *transaction.TradeState {
		case "SUCCESS":
			if validateWechatTransaction(transaction, topUp, cfg) != nil {
				continue
			}
			transactionID := ""
			if transaction.TransactionId != nil {
				transactionID = *transaction.TransactionId
			}
			refreshed, err = store.credit(topUp.TradeNo, transactionID, clientIP)
		case "CLOSED", "REVOKED", "PAYERROR":
			refreshed, err = store.fail(topUp.TradeNo)
		default:
			continue
		}
		if err == nil && refreshed != nil {
			*topUp = *refreshed
		}
	}
}

func reconcileWechatPendingTopUps(
	ctx context.Context,
	userID int,
	topups []*model.TopUp,
	clientIP string,
) {
	cfg := setting.GetWechatPayConfig()
	if cfg.Validate() != nil {
		return
	}
	service, err := getWechatPayService(ctx, cfg)
	if err != nil {
		return
	}
	reconcileWechatPendingTopUpsWithBudget(
		ctx,
		userID,
		topups,
		cfg,
		service,
		modelWechatOrderSyncStore{},
		clientIP,
		wechatOrderSyncBudget{Limit: wechatPayListSyncLimit, Timeout: wechatPayListSyncTimeout},
	)
}

func ptr[T any](value T) *T { return &value }

func moneyToCents(money float64) (int64, error) {
	amount := decimal.NewFromFloat(money).Round(2).Shift(2)
	if amount.LessThan(decimal.NewFromInt(1)) {
		return 0, errors.New("支付金额必须至少为 1 分")
	}
	return amount.IntPart(), nil
}

func newWechatPayTradeNo(userID int) (string, error) {
	randomBytes := make([]byte, 2)
	if _, err := rand.Read(randomBytes); err != nil {
		return "", err
	}
	tradeNo := fmt.Sprintf("WX-%d-%x-%s", userID, time.Now().UnixMilli(), strings.ToUpper(hex.EncodeToString(randomBytes)))
	if len(tradeNo) > 32 {
		return "", errors.New("生成的微信支付订单号过长")
	}
	return tradeNo, nil
}

func getWechatPayClient(ctx context.Context, cfg setting.WechatPayConfig) (*core.Client, error) {
	wechatPayClientMu.Lock()
	defer wechatPayClientMu.Unlock()

	if wechatPayCachedClient != nil && wechatPayCachedConfig == cfg {
		return wechatPayCachedClient, nil
	}

	privateKey, err := utils.LoadPrivateKeyWithPath(cfg.PrivateKeyPath)
	if err != nil {
		return nil, fmt.Errorf("加载微信支付商户私钥失败: %w", err)
	}
	publicKey, err := utils.LoadPublicKeyWithPath(cfg.PublicKeyPath)
	if err != nil {
		return nil, fmt.Errorf("加载微信支付公钥失败: %w", err)
	}
	client, err := core.NewClient(ctx, option.WithWechatPayPublicKeyAuthCipher(
		cfg.MchID,
		cfg.CertSerialNo,
		privateKey,
		cfg.PublicKeyID,
		publicKey,
	))
	if err != nil {
		return nil, fmt.Errorf("初始化微信支付客户端失败: %w", err)
	}
	wechatPayCachedClient = client
	wechatPayCachedConfig = cfg
	return client, nil
}

func getWechatPayService(ctx context.Context, cfg setting.WechatPayConfig) (*native.NativeApiService, error) {
	client, err := getWechatPayClient(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return &native.NativeApiService{Client: client}, nil
}

func normalizeWechatTopUpAmount(amount int64) int64 {
	if operation_setting.GetQuotaDisplayType() != operation_setting.QuotaDisplayTypeTokens {
		return amount
	}
	return decimal.NewFromInt(amount).Div(decimal.NewFromFloat(common.QuotaPerUnit)).IntPart()
}

func RequestWechatNativePay(c *gin.Context) {
	cfg := setting.GetWechatPayConfig()
	if err := cfg.Validate(); err != nil {
		common.ApiErrorMsg(c, "微信支付配置不可用")
		return
	}

	var req wechatPayRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		common.ApiErrorMsg(c, "参数错误")
		return
	}
	if req.Amount < getMinTopup() {
		common.ApiErrorMsg(c, fmt.Sprintf("充值数量不能小于 %d", getMinTopup()))
		return
	}
	if req.Amount > 10000 {
		common.ApiErrorMsg(c, "充值数量不能大于 10000")
		return
	}

	userID := c.GetInt("id")
	group, err := model.GetUserGroup(userID, true)
	if err != nil {
		common.ApiErrorMsg(c, "获取用户分组失败")
		return
	}
	payMoney := getPayMoney(req.Amount, group)
	amountCents, err := moneyToCents(payMoney)
	if err != nil {
		common.ApiErrorMsg(c, "充值金额过低或无效")
		return
	}
	tradeNo, err := newWechatPayTradeNo(userID)
	if err != nil {
		common.ApiErrorMsg(c, "生成订单号失败")
		return
	}

	topUp := &model.TopUp{
		UserId:          userID,
		Amount:          normalizeWechatTopUpAmount(req.Amount),
		Money:           decimal.NewFromInt(amountCents).Div(decimal.NewFromInt(100)).InexactFloat64(),
		TradeNo:         tradeNo,
		PaymentMethod:   model.PaymentMethodWechatPay,
		PaymentProvider: model.PaymentProviderWechatPay,
		CreateTime:      time.Now().Unix(),
		Status:          common.TopUpStatusPending,
	}
	if topUp.Amount <= 0 {
		common.ApiErrorMsg(c, "充值数量无效")
		return
	}
	if err := topUp.Insert(); err != nil {
		logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付创建本地订单失败 trade_no=%s error_type=database", tradeNo))
		common.ApiErrorMsg(c, "创建订单失败")
		return
	}

	service, err := getWechatPayService(c.Request.Context(), cfg)
	if err != nil {
		_ = model.UpdatePendingTopUpStatus(tradeNo, model.PaymentProviderWechatPay, common.TopUpStatusFailed)
		logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付客户端初始化失败 trade_no=%s error_type=configuration", tradeNo))
		common.ApiErrorMsg(c, "微信支付配置不可用")
		return
	}
	expiresAt := time.Now().Add(wechatPayOrderTTL).Truncate(time.Second)
	currency := "CNY"
	response, _, err := service.Prepay(c.Request.Context(), native.PrepayRequest{
		Appid:       ptr(cfg.AppID),
		Mchid:       ptr(cfg.MchID),
		Description: ptr(fmt.Sprintf("账户充值 %d", req.Amount)),
		OutTradeNo:  ptr(tradeNo),
		TimeExpire:  ptr(expiresAt),
		NotifyUrl:   ptr(cfg.NotifyURL),
		Amount: &native.Amount{
			Total:    ptr(amountCents),
			Currency: ptr(currency),
		},
	})
	if err != nil || response == nil || response.CodeUrl == nil || *response.CodeUrl == "" {
		_ = model.UpdatePendingTopUpStatus(tradeNo, model.PaymentProviderWechatPay, common.TopUpStatusFailed)
		logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付预下单失败 trade_no=%s error_type=upstream", tradeNo))
		common.ApiErrorMsg(c, "拉起支付失败")
		return
	}

	logger.LogInfo(c.Request.Context(), fmt.Sprintf("微信支付订单创建成功 trade_no=%s status=pending", tradeNo))
	common.ApiSuccess(c, gin.H{
		"code_url":     *response.CodeUrl,
		"trade_no":     tradeNo,
		"amount_cents": amountCents,
		"expires_at":   expiresAt.Unix(),
	})
}

func validateWechatTransaction(transaction *payments.Transaction, topUp *model.TopUp, cfg setting.WechatPayConfig) error {
	if transaction == nil || topUp == nil {
		return wechatPayValidationError("missing_transaction_or_order")
	}
	if topUp.PaymentProvider != model.PaymentProviderWechatPay || topUp.PaymentMethod != model.PaymentMethodWechatPay {
		return wechatPayValidationError("payment_provider_or_method_mismatch")
	}
	if transaction.Appid == nil || *transaction.Appid != cfg.AppID {
		return wechatPayValidationError("appid_mismatch")
	}
	if transaction.Mchid == nil || *transaction.Mchid != cfg.MchID {
		return wechatPayValidationError("mchid_mismatch")
	}
	if transaction.OutTradeNo == nil || *transaction.OutTradeNo != topUp.TradeNo {
		return wechatPayValidationError("trade_no_mismatch")
	}
	if transaction.TradeState == nil || *transaction.TradeState != "SUCCESS" {
		return wechatPayValidationError("trade_state_not_success")
	}
	if transaction.Amount == nil || transaction.Amount.Total == nil || transaction.Amount.Currency == nil {
		return wechatPayValidationError("amount_missing")
	}
	expectedCents, err := moneyToCents(topUp.Money)
	if err != nil || *transaction.Amount.Total != expectedCents {
		return wechatPayValidationError("amount_mismatch")
	}
	if *transaction.Amount.Currency != "CNY" {
		return wechatPayValidationError("currency_mismatch")
	}
	return nil
}

func GetWechatPayOrder(c *gin.Context) {
	cfg := setting.GetWechatPayConfig()
	if err := cfg.Validate(); err != nil {
		common.ApiErrorMsg(c, "微信支付配置不可用")
		return
	}
	tradeNo := c.Param("trade_no")
	topUp := model.GetTopUpByTradeNo(tradeNo)
	if topUp == nil || topUp.UserId != c.GetInt("id") || topUp.PaymentProvider != model.PaymentProviderWechatPay || topUp.PaymentMethod != model.PaymentMethodWechatPay {
		c.JSON(http.StatusNotFound, gin.H{"success": false, "message": "订单不存在"})
		return
	}
	if topUp.Status == common.TopUpStatusSuccess {
		common.ApiSuccess(c, gin.H{
			"trade_no":      tradeNo,
			"local_status":  topUp.Status,
			"wechat_status": "SUCCESS",
		})
		return
	}
	if topUp.Status != common.TopUpStatusPending {
		common.ApiSuccess(c, gin.H{
			"trade_no":      tradeNo,
			"local_status":  topUp.Status,
			"wechat_status": "NOT_QUERYABLE",
		})
		return
	}

	service, err := getWechatPayService(c.Request.Context(), cfg)
	if err != nil {
		logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付查单客户端初始化失败 trade_no=%s error_type=configuration", tradeNo))
		common.ApiErrorMsg(c, "微信支付配置不可用")
		return
	}
	transaction, _, err := service.QueryOrderByOutTradeNo(c.Request.Context(), native.QueryOrderByOutTradeNoRequest{
		OutTradeNo: ptr(tradeNo),
		Mchid:      ptr(cfg.MchID),
	})
	if err != nil || transaction == nil {
		logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付查单失败 trade_no=%s error_type=upstream", tradeNo))
		common.ApiErrorMsg(c, "微信支付查单失败")
		return
	}

	wechatStatus := "UNKNOWN"
	if transaction.TradeState != nil {
		wechatStatus = *transaction.TradeState
	}
	if wechatStatus == "SUCCESS" {
		if validationErr := validateWechatTransaction(transaction, topUp, cfg); validationErr != nil {
			logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付查单字段校验失败 trade_no=%s status=%s error_type=%s", tradeNo, wechatStatus, validationErr.Error()))
			common.ApiErrorMsg(c, "微信支付订单校验失败")
			return
		}
		transactionID := ""
		if transaction.TransactionId != nil {
			transactionID = *transaction.TransactionId
		}
		if rechargeErr := model.RechargeWechatPay(tradeNo, transactionID, c.ClientIP()); rechargeErr != nil {
			logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付查单补单失败 trade_no=%s status=%s error_type=recharge", tradeNo, wechatStatus))
			common.ApiErrorMsg(c, "订单入账失败")
			return
		}
		topUp = model.GetTopUpByTradeNo(tradeNo)
	} else if wechatStatus == "CLOSED" || wechatStatus == "REVOKED" || wechatStatus == "PAYERROR" {
		if updateErr := model.UpdatePendingTopUpStatus(tradeNo, model.PaymentProviderWechatPay, common.TopUpStatusFailed); updateErr == nil {
			topUp = model.GetTopUpByTradeNo(tradeNo)
		}
	}

	if topUp == nil {
		common.ApiErrorMsg(c, "订单状态读取失败")
		return
	}
	common.ApiSuccess(c, gin.H{
		"trade_no":      tradeNo,
		"local_status":  topUp.Status,
		"wechat_status": wechatStatus,
	})
}

func limitWechatPayNotifyBody(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, wechatPayNotifyMaxBodyBytes)
}

func WechatPayNotify(c *gin.Context) {
	cfg := setting.GetWechatPayConfig()
	if err := cfg.Validate(); err != nil {
		wechatNotifyFailure(c, http.StatusServiceUnavailable, "configuration_unavailable")
		return
	}
	if _, err := getWechatPayClient(c.Request.Context(), cfg); err != nil {
		logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付客户端初始化失败 detail=%v", err))
		wechatNotifyFailure(c, http.StatusInternalServerError, "client_initialization")
		return
	}
	publicKey, err := utils.LoadPublicKeyWithPath(cfg.PublicKeyPath)
	if err != nil {
		logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付公钥加载失败 detail=%v", err))
		wechatNotifyFailure(c, http.StatusInternalServerError, "client_initialization")
		return
	}
	handler := notify.NewNotifyHandler(cfg.APIv3Key, verifiers.NewSHA256WithRSAPubkeyVerifier(cfg.PublicKeyID, *publicKey))
	transaction := new(payments.Transaction)
	limitWechatPayNotifyBody(c)
	if _, err := handler.ParseNotifyRequest(c.Request.Context(), c.Request, transaction); err != nil {
		wechatNotifyFailure(c, http.StatusBadRequest, "signature_or_decryption")
		return
	}

	tradeNo := "unknown"
	status := "unknown"
	if transaction.OutTradeNo != nil {
		tradeNo = *transaction.OutTradeNo
	}
	if transaction.TradeState != nil {
		status = *transaction.TradeState
	}
	topUp := model.GetTopUpByTradeNo(tradeNo)
	if err := validateWechatTransaction(transaction, topUp, cfg); err != nil {
		logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付回调字段校验失败 trade_no=%s status=%s error_type=%s", tradeNo, status, err.Error()))
		wechatNotifyFailure(c, http.StatusBadRequest, "invalid_order")
		return
	}
	transactionID := ""
	if transaction.TransactionId != nil {
		transactionID = *transaction.TransactionId
	}
	if err := model.RechargeWechatPay(tradeNo, transactionID, c.ClientIP()); err != nil {
		logger.LogError(c.Request.Context(), fmt.Sprintf("微信支付回调入账失败 trade_no=%s status=%s error_type=recharge", tradeNo, status))
		wechatNotifyFailure(c, http.StatusInternalServerError, "recharge_failed")
		return
	}
	logger.LogInfo(c.Request.Context(), fmt.Sprintf("微信支付回调处理成功 trade_no=%s status=%s", tradeNo, status))
	c.JSON(http.StatusOK, gin.H{"code": "SUCCESS", "message": "成功"})
}

func wechatNotifyFailure(c *gin.Context, status int, errorType string) {
	logger.LogWarn(c.Request.Context(), fmt.Sprintf("微信支付回调拒绝 error_type=%s", errorType))
	c.JSON(status, gin.H{"code": "FAIL", "message": "失败"})
}
