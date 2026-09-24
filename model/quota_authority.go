package model

import (
	"errors"
	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
	"math"
)

// ReserveUserQuota checks spendable funds in the same statement that reserves them.
// Final settlement deliberately continues using DecreaseUserQuota to represent actual debt.
func ReserveUserQuota(id, amount int) error {
	if !common.IsQuotaDBAuthoritative() {
		return DecreaseUserQuota(id, amount, false)
	}
	if amount < 0 || amount > common.MaxQuota {
		return errors.New("invalid reservation")
	}
	if amount == 0 {
		return nil
	}
	result := DB.Model(&User{}).Where("id = ? AND status = ? AND quota >= ?", id, common.UserStatusEnabled, amount).Update("quota", gorm.Expr("quota - ?", amount))
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("insufficient user quota")
	}
	return nil
}

// ReserveTokenQuota atomically honors finite limits; unlimited tokens still track usage.
func ReserveTokenQuota(id, amount int) error {
	if amount < 0 || amount > common.MaxQuota {
		return errors.New("invalid reservation")
	}
	if amount == 0 {
		return nil
	}
	result := DB.Model(&Token{}).Where("id = ? AND status = ? AND (expired_time = -1 OR expired_time >= ?) AND (unlimited_quota = ? OR remain_quota >= ?) AND used_quota <= ? AND remain_quota >= ?", id, common.TokenStatusEnabled, common.GetTimestamp(), true, amount, int64(math.MaxInt64)-int64(amount), int64(math.MinInt64)+int64(amount)).Updates(map[string]interface{}{"remain_quota": gorm.Expr("remain_quota - ?", amount), "used_quota": gorm.Expr("used_quota + ?", amount), "accessed_time": common.GetTimestamp()})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("insufficient token quota")
	}
	return nil
}
