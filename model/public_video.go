package model

import (
	"errors"
	"math"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
)

// PublicVideoTask is the durable account ledger for isolated video execution.
// Monetary updates and the settlement receipt commit together, never from a webhook alone.
type PublicVideoTask struct {
	ID              string `gorm:"primaryKey;size:48"`
	UserID          int    `gorm:"index"`
	TokenID         int
	ClientRequestID string `gorm:"size:128"`
	Fingerprint     string `gorm:"size:64"`
	Model           string `gorm:"size:100"`
	Group           string `gorm:"size:64"`
	Body            string `gorm:"type:text"`
	BackendID       string `gorm:"size:48"`
	SubmitAttempts  int
	State           string `gorm:"size:32;index"`
	ReservedCNY     string `gorm:"size:32"`
	ChargedCNY      string `gorm:"size:32"`
	QuotaPerCNY     string `gorm:"size:32"`
	ReservedQuota   int
	ChargedQuota    int
	Snapshot        string `gorm:"type:text"`
	LastError       string `gorm:"size:120"`
	CreatedAt       int64
	UpdatedAt       int64
}

var ErrPublicVideoConflict = errors.New("idempotency_conflict")
var ErrPublicVideoQuota = errors.New("insufficient_quota")
var ErrPublicVideoForbidden = errors.New("access_denied")

// PublicVideoQuota converts a frozen CNY amount into bounded, integer site quota.
func PublicVideoQuota(amount, rate string) (int, error) {
	a, e := decimal.NewFromString(amount)
	if e != nil || a.IsNegative() || a.GreaterThan(decimal.NewFromInt(100000)) {
		return 0, errors.New("invalid_amount")
	}
	r, e := decimal.NewFromString(rate)
	if e != nil || !r.IsPositive() || r.GreaterThan(decimal.NewFromInt(100000000)) {
		return 0, errors.New("invalid_quota_rate")
	}
	q, clamp := common.QuotaFromDecimalChecked(a.Mul(r).Ceil())
	if clamp != nil {
		return 0, clamp
	}
	return q, nil
}

// ReservePublicVideo serializes all requests for an owner before checking idempotency and funds.
func ReservePublicVideo(candidate PublicVideoTask) (PublicVideoTask, bool, error) {
	if !common.IsQuotaDBAuthoritative() {
		return PublicVideoTask{}, false, errors.New("native_quota_authority_required")
	}
	var result PublicVideoTask
	reused := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var u User
		if e := lockForUpdate(tx).First(&u, candidate.UserID).Error; e != nil {
			return e
		}
		var token Token
		if e := lockForUpdate(tx).First(&token, candidate.TokenID).Error; e != nil {
			return e
		}
		if u.Status != common.UserStatusEnabled || token.UserId != u.Id || (token.Status != common.TokenStatusEnabled && token.Status != common.TokenStatusExhausted) || (token.ExpiredTime != -1 && token.ExpiredTime < time.Now().Unix()) {
			return ErrPublicVideoForbidden
		}
		if token.ModelLimitsEnabled && !token.GetModelLimitsMap()[candidate.Model] {
			return ErrPublicVideoForbidden
		}
		e := tx.First(&result, "id = ? AND user_id = ?", candidate.ID, u.Id).Error
		if e == nil {
			if result.Fingerprint != candidate.Fingerprint {
				return ErrPublicVideoConflict
			}
			reused = true
			return nil
		}
		if !errors.Is(e, gorm.ErrRecordNotFound) {
			return e
		}
		if token.Status != common.TokenStatusEnabled {
			return ErrPublicVideoQuota
		}
		quota, e := PublicVideoQuota(candidate.ReservedCNY, candidate.QuotaPerCNY)
		if e != nil || quota <= 0 {
			return errors.New("invalid_reservation")
		}
		if u.Quota < quota || (!token.UnlimitedQuota && token.RemainQuota < quota) {
			return ErrPublicVideoQuota
		}
		if !quotaLedgerAdditionFits(token.UsedQuota, quota) {
			return errors.New("quota_overflow")
		}
		if !quotaLedgerAdditionFits(token.RemainQuota, -quota) {
			return errors.New("quota_overflow")
		}
		candidate.ReservedQuota = quota
		candidate.State = "reserved"
		candidate.CreatedAt = time.Now().Unix()
		candidate.UpdatedAt = candidate.CreatedAt
		if e = tx.Model(&u).Update("quota", gorm.Expr("quota - ?", quota)).Error; e != nil {
			return e
		}
		changes := map[string]interface{}{"used_quota": gorm.Expr("used_quota + ?", quota), "accessed_time": time.Now().Unix()}
		changes["remain_quota"] = gorm.Expr("remain_quota - ?", quota)
		if e = tx.Model(&token).Updates(changes).Error; e != nil {
			return e
		}
		if e = tx.Create(&candidate).Error; e != nil {
			return e
		}
		result = candidate
		return nil
	})
	return result, reused, err
}

// SettlePublicVideo applies one authoritative final amount, including a proven full refund.
func SettlePublicVideo(id, amount, snapshot string) error {
	return settlePublicVideo(id, amount, snapshot, false)
}

// SettlePublicVideoNoTask releases only a currently first-attempt reservation.
// Counter/state/backend checks occur while holding the same transaction row lock
// as the wallet update, so another worker cannot submit after a stale refund.
func SettlePublicVideoNoTask(id, snapshot string) error {
	return settlePublicVideo(id, "0.000000", snapshot, true)
}

func settlePublicVideo(id, amount, snapshot string, requireNoTask bool) error {
	if !common.IsQuotaDBAuthoritative() {
		return errors.New("native_quota_authority_required")
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var row PublicVideoTask
		if e := lockForUpdate(tx).First(&row, "id = ?", id).Error; e != nil {
			return e
		}
		if row.State == "settled" {
			if row.ChargedCNY != amount {
				return ErrPublicVideoConflict
			}
			return nil
		}
		if requireNoTask && (row.SubmitAttempts != 1 || row.BackendID != "" || row.State != "reserved") {
			return errors.New("no_task_settlement_guard_changed")
		}
		if row.State != "reserved" && row.State != "submitted" && row.State != "pending_review" {
			return errors.New("invalid_settlement_state")
		}
		quota, e := PublicVideoQuota(amount, row.QuotaPerCNY)
		if e != nil {
			return e
		}
		var u User
		if e = lockForUpdate(tx.Unscoped()).First(&u, row.UserID).Error; e != nil {
			return e
		}
		var token Token
		if e = lockForUpdate(tx.Unscoped()).First(&token, row.TokenID).Error; e != nil {
			return e
		}
		if token.UserId != u.Id {
			return ErrPublicVideoForbidden
		}
		delta := row.ReservedQuota - quota
		if delta < 0 {
			estimated, err := publicVideoUsesEstimatedReservation(row)
			if err != nil {
				return err
			}
			// New operator estimates are not upper bounds. Preserve the legacy
			// verified debt contract, but require funds before supplementing an
			// estimated reservation; no wallet/log/receipt changes on refusal.
			if estimated && (u.Quota < -delta || (!token.UnlimitedQuota && token.RemainQuota < -delta)) {
				return ErrPublicVideoQuota
			}
		}
		if !quotaLedgerAdditionFits(u.Quota, delta) || !quotaLedgerAdditionFits(u.UsedQuota, quota) || !quotaLedgerAdditionFits(token.UsedQuota, -delta) {
			return errors.New("quota_overflow")
		}
		if !quotaLedgerAdditionFits(token.RemainQuota, delta) {
			return errors.New("quota_overflow")
		}
		if e = tx.Unscoped().Model(&u).Updates(map[string]interface{}{"quota": gorm.Expr("quota + ?", delta), "used_quota": gorm.Expr("used_quota + ?", quota), "request_count": gorm.Expr("request_count + 1")}).Error; e != nil {
			return e
		}
		changes := map[string]interface{}{"used_quota": gorm.Expr("used_quota - ?", delta)}
		changes["remain_quota"] = gorm.Expr("remain_quota + ?", delta)
		if e = tx.Unscoped().Model(&token).Updates(changes).Error; e != nil {
			return e
		}
		// This service requires the existing logs table to share the main transaction database.
		other, _ := common.Marshal(map[string]interface{}{"billing_source": "wallet", "billing_contract_version": "xtai-video-billing-v2.2", "charged_cny": amount, "reserved_cny": row.ReservedCNY, "quota_per_cny": row.QuotaPerCNY, "public_video_id": row.ID})
		log := Log{UserId: u.Id, CreatedAt: time.Now().Unix(), Type: LogTypeConsume, Content: "Video actual-cost settlement", Username: u.Username, TokenName: token.Name, ModelName: row.Model, Quota: quota, TokenId: token.Id, Group: row.Group, RequestId: row.ID, UpstreamRequestId: row.BackendID, Other: string(other)}
		// Production's older schema calls this column channel_id; newer versions use channel.
		// The dedicated task ledger and log metadata carry the provider identity independently.
		if e = tx.Omit("ChannelId").Create(&log).Error; e != nil {
			return e
		}
		return tx.Model(&row).Updates(map[string]interface{}{"state": "settled", "charged_cny": amount, "charged_quota": quota, "snapshot": snapshot, "updated_at": time.Now().Unix(), "last_error": ""}).Error
	})
}

// publicVideoUsesEstimatedReservation reads only the frozen private envelope
// written by the public frontdoor. A malformed envelope must not silently fall
// back to legacy debt settlement. Full quote/fingerprint integrity is checked by
// the frontdoor before settlement; this model boundary enforces wallet safety.
func publicVideoUsesEstimatedReservation(row PublicVideoTask) (bool, error) {
	if row.Body == "" {
		return false, nil
	}
	if len(row.Body) > 1024*1024 {
		return false, errors.New("invalid_estimated_reservation")
	}
	var body map[string]interface{}
	if common.UnmarshalJsonStr(row.Body, &body) != nil {
		if strings.Contains(row.Body, "_public_reservation") {
			return false, errors.New("invalid_estimated_reservation")
		}
		return false, nil
	}
	value, exists := body["_public_reservation"]
	if !exists {
		return false, nil
	}
	metadata, ok := value.(map[string]interface{})
	if !ok || metadata["schema_version"] != "xtai-public-video-estimated-reservation-v1" || metadata["price_source"] != "nodyhub_operator_testing_estimate" || metadata["pricing_kind"] != "estimated_reservation" || metadata["verification_status"] != "unverified" || metadata["is_upper_bound"] != false || metadata["reserved_cny_exact"] != row.ReservedCNY || metadata["payload_fingerprint"] != row.Fingerprint {
		return false, errors.New("invalid_estimated_reservation")
	}
	return true, nil
}

// Aggregate wallet ledgers can exceed the per-request int32 charge bound.
// Check before adding, since an overflowed sum cannot be validated afterward.
func quotaLedgerAdditionFits(balance, delta int) bool {
	if delta > 0 {
		return int64(balance) <= math.MaxInt64-int64(delta)
	}
	if delta < 0 {
		return int64(balance) >= math.MinInt64-int64(delta)
	}
	return true
}
