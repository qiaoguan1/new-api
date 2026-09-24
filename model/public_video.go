package model

import (
	"context"
	"errors"
	"fmt"
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

// PublicVideoCacheEvent delivers quota deltas without overwriting native batched accounting.
type PublicVideoCacheEvent struct {
	ID        string `gorm:"primaryKey;size:64"`
	UserID    int
	TokenID   int
	Delta     int
	Delivered bool `gorm:"index"`
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
		if int64(token.UsedQuota)+int64(quota) > common.MaxQuota {
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
		if !token.UnlimitedQuota {
			changes["remain_quota"] = gorm.Expr("remain_quota - ?", quota)
		}
		if e = tx.Model(&token).Updates(changes).Error; e != nil {
			return e
		}
		if e = tx.Create(&candidate).Error; e != nil {
			return e
		}
		result = candidate
		return tx.Create(&PublicVideoCacheEvent{ID: candidate.ID + ":reserve", UserID: u.Id, TokenID: token.Id, Delta: -quota}).Error
	})
	return result, reused, err
}

// SettlePublicVideo applies one authoritative final amount, including a proven full refund.
func SettlePublicVideo(id, amount, snapshot string) error {
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
		if int64(u.Quota)+int64(delta) > common.MaxQuota || int64(u.Quota)+int64(delta) < common.MinQuota || int64(u.UsedQuota)+int64(quota) > common.MaxQuota || int64(token.UsedQuota)-int64(delta) > common.MaxQuota {
			return errors.New("quota_overflow")
		}
		if !token.UnlimitedQuota && (int64(token.RemainQuota)+int64(delta) > common.MaxQuota || int64(token.RemainQuota)+int64(delta) < common.MinQuota) {
			return errors.New("quota_overflow")
		}
		if e = tx.Unscoped().Model(&u).Updates(map[string]interface{}{"quota": gorm.Expr("quota + ?", delta), "used_quota": gorm.Expr("used_quota + ?", quota), "request_count": gorm.Expr("request_count + 1")}).Error; e != nil {
			return e
		}
		changes := map[string]interface{}{"used_quota": gorm.Expr("used_quota - ?", delta)}
		if !token.UnlimitedQuota {
			changes["remain_quota"] = gorm.Expr("remain_quota + ?", delta)
		}
		if e = tx.Unscoped().Model(&token).Updates(changes).Error; e != nil {
			return e
		}
		// This service requires the existing logs table to share the main transaction database.
		other, _ := common.Marshal(map[string]interface{}{"billing_source": "wallet", "billing_contract_version": "xtai-video-billing-v2.2", "charged_cny": amount, "reserved_cny": row.ReservedCNY, "quota_per_cny": row.QuotaPerCNY, "public_video_id": row.ID})
		log := Log{UserId: u.Id, CreatedAt: time.Now().Unix(), Type: LogTypeConsume, Content: "Video actual-cost settlement", Username: u.Username, TokenName: token.Name, ModelName: row.Model, Quota: quota, TokenId: token.Id, Group: row.Group, RequestId: row.ID, UpstreamRequestId: row.BackendID, Other: string(other)}
		if e = tx.Create(&log).Error; e != nil {
			return e
		}
		if e = tx.Create(&PublicVideoCacheEvent{ID: row.ID + ":settle", UserID: u.Id, TokenID: token.Id, Delta: delta}).Error; e != nil {
			return e
		}
		return tx.Model(&row).Updates(map[string]interface{}{"state": "settled", "charged_cny": amount, "charged_quota": quota, "snapshot": snapshot, "updated_at": time.Now().Unix(), "last_error": ""}).Error
	})
}

// FlushPublicVideoCache uses a Redis receipt to make replayed delta delivery safe.
func FlushPublicVideoCache(taskID string) error {
	if !common.RedisEnabled {
		return errors.New("redis_required")
	}
	var events []PublicVideoCacheEvent
	q := DB.Where("delivered = ?", false)
	if taskID != "" {
		q = q.Where("id IN ?", []string{taskID + ":reserve", taskID + ":settle"})
	}
	if e := q.Order("id asc").Limit(100).Find(&events).Error; e != nil {
		return e
	}
	for _, event := range events {
		var token Token
		if e := DB.Unscoped().First(&token, event.TokenID).Error; e != nil {
			return e
		}
		keys := []string{"public-video-cache:" + event.ID, fmt.Sprintf("user:%d", event.UserID), "token:" + common.GenerateHMAC(token.Key)}
		script := `if redis.call('exists',KEYS[1])==1 then return 0 end
if redis.call('exists',KEYS[2])==1 then redis.call('hincrby',KEYS[2],'Quota',ARGV[1]) end
if ARGV[2]=='1' and redis.call('exists',KEYS[3])==1 then redis.call('hincrby',KEYS[3],'RemainQuota',ARGV[1]) end
redis.call('set',KEYS[1],'1');return 1`
		finite := "1"
		if token.UnlimitedQuota {
			finite = "0"
		}
		if e := common.RDB.Eval(context.Background(), script, keys, event.Delta, finite).Err(); e != nil {
			return e
		}
		if e := DB.Model(&event).Update("delivered", true).Error; e != nil {
			return e
		}
	}
	return nil
}
