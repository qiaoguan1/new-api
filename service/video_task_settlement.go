package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const VideoBillingContractVersion = constant.XingTuVideoContractCurrent

var (
	ErrVideoSettlementInvalid         = errors.New("invalid video settlement evidence")
	ErrVideoSettlementConflict        = errors.New("video settlement conflict")
	ErrVideoSettlementTaskNotReady    = errors.New("video task is not ready for settlement")
	ErrVideoSettlementPaymentRequired = errors.New("video settlement exceeds a hard quota limit")
)

type VideoSettlementEvidence struct {
	ContractVersion     string `json:"contract_version"`
	SettlementID        string `json:"settlement_id"`
	JobID               string `json:"job_id"`
	Revision            int    `json:"revision"`
	ProviderTaskID      string `json:"provider_task_id"`
	ActualCostStatus    string `json:"actual_cost_status"`
	ActualCostCNYExact  string `json:"actual_cost_cny_exact"`
	EvidenceSource      string `json:"evidence_source"`
	EvidenceID          string `json:"evidence_id"`
	ObservedAt          string `json:"observed_at"`
	EvidenceFingerprint string `json:"evidence_fingerprint"`
}

type VideoSettlementOutcome struct {
	TaskID           string `json:"task_id"`
	Revision         int    `json:"revision"`
	BillingStatus    string `json:"billing_status"`
	ChargedAmount    string `json:"charged_amount"`
	RefundAmount     string `json:"refund_amount"`
	SupplementAmount string `json:"supplement_amount"`
	Applied          bool   `json:"applied"`
	Replay           bool   `json:"replay"`
}

type PendingVideoSettlementTask struct {
	ContractVersion string `json:"contract_version"`
	JobID           string `json:"job_id"`
	ProviderTaskID  string `json:"provider_task_id"`
	ChannelID       int    `json:"channel_id"`
	Revision        int    `json:"next_revision"`
}

func ListPendingVideoSettlementTasks(limit int) ([]PendingVideoSettlementTask, error) {
	if limit < 1 || limit > 5000 {
		limit = 1000
	}
	result := make([]PendingVideoSettlementTask, 0, limit)
	const batchSize = 1000
	var cursor int64
	for len(result) < limit {
		var tasks []model.Task
		query := model.DB.Where("status = ?", model.TaskStatusSuccess)
		if cursor > 0 {
			query = query.Where("id < ?", cursor)
		}
		if err := query.Order("id desc").Limit(batchSize).Find(&tasks).Error; err != nil {
			return nil, err
		}
		if len(tasks) == 0 {
			break
		}
		cursor = tasks[len(tasks)-1].ID
		for i := range tasks {
			billing := tasks[i].PrivateData.BillingContext
			if billing == nil || !constant.IsXingTuVideoContract(billing.ContractVersion) ||
				(billing.BillingStatus != "settlement_pending" && billing.BillingStatus != "payment_required" && billing.BillingStatus != "pending_review") {
				continue
			}
			result = append(result, PendingVideoSettlementTask{
				ContractVersion: billing.ContractVersion,
				JobID:           tasks[i].TaskID,
				ProviderTaskID:  tasks[i].GetUpstreamTaskID(),
				ChannelID:       tasks[i].ChannelId,
				Revision:        billing.SettlementRevision + 1,
			})
			if len(result) >= limit {
				break
			}
		}
		if len(tasks) < batchSize {
			break
		}
	}
	return result, nil
}

func settlementDigest(parts ...string) string {
	h := sha256.New()
	for i, part := range parts {
		if i > 0 {
			h.Write([]byte{0})
		}
		h.Write([]byte(part))
	}
	return hex.EncodeToString(h.Sum(nil))
}

func expectedVideoEvidenceFingerprint(e VideoSettlementEvidence) string {
	return settlementDigest(
		e.ContractVersion,
		e.JobID,
		e.ProviderTaskID,
		e.ActualCostStatus,
		e.ActualCostCNYExact,
		e.EvidenceSource,
		e.EvidenceID,
		e.ObservedAt,
	)
}

func expectedVideoSettlementID(e VideoSettlementEvidence) string {
	return settlementDigest(
		"xtai-video-settlement-v2",
		e.JobID,
		strconv.Itoa(e.Revision),
		e.EvidenceFingerprint,
	)
}

func parseCNYMicrosExact(value string) (int64, error) {
	value = strings.TrimSpace(value)
	if value == "" || strings.HasPrefix(value, "+") || strings.HasPrefix(value, "-") {
		return 0, ErrVideoSettlementInvalid
	}
	parts := strings.Split(value, ".")
	if len(parts) > 2 || parts[0] == "" {
		return 0, ErrVideoSettlementInvalid
	}
	for _, r := range parts[0] {
		if r < '0' || r > '9' {
			return 0, ErrVideoSettlementInvalid
		}
	}
	whole, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil || whole > 100000 {
		return 0, ErrVideoSettlementInvalid
	}
	fraction := ""
	if len(parts) == 2 {
		fraction = parts[1]
		if fraction == "" || len(fraction) > 6 {
			return 0, ErrVideoSettlementInvalid
		}
		for _, r := range fraction {
			if r < '0' || r > '9' {
				return 0, ErrVideoSettlementInvalid
			}
		}
	}
	fraction += strings.Repeat("0", 6-len(fraction))
	fractionMicros := int64(0)
	if fraction != "" {
		fractionMicros, err = strconv.ParseInt(fraction, 10, 64)
		if err != nil {
			return 0, ErrVideoSettlementInvalid
		}
	}
	return whole*1_000_000 + fractionMicros, nil
}

func videoChargeQuota(actualCostMicros int64) (int, error) {
	// ceil(actual CNY * 1.5 * 500000 quota/CNY). With micro-CNY input this
	// reduces exactly to ceil(actualCostMicros * 3 / 4).
	if actualCostMicros < 0 || actualCostMicros > int64(^uint(0)>>1)/3 {
		return 0, ErrVideoSettlementInvalid
	}
	quota64 := (actualCostMicros*3 + 3) / 4
	if quota64 > int64(^uint(0)>>1) {
		return 0, ErrVideoSettlementInvalid
	}
	return int(quota64), nil
}

func quotaCNYExact(quota int) string {
	if quota < 0 {
		quota = -quota
	}
	micros := int64(quota) * 2
	return fmt.Sprintf("%d.%06d", micros/1_000_000, micros%1_000_000)
}

func validateVideoSettlementEvidence(e VideoSettlementEvidence) (int, error) {
	approvedSource := e.EvidenceSource == "provider_account_ledger" ||
		e.EvidenceSource == "newapi_authenticated_video_task" ||
		e.EvidenceSource == "toonflow_web_operation_log"
	if !constant.IsXingTuVideoContract(e.ContractVersion) || e.Revision < 1 ||
		!strings.HasPrefix(e.JobID, "task_") || len(e.JobID) > 191 ||
		strings.TrimSpace(e.ProviderTaskID) == "" || len(e.ProviderTaskID) > 512 ||
		strings.TrimSpace(e.EvidenceID) == "" || len(e.EvidenceID) > 191 ||
		len(e.ObservedAt) > 64 || len(e.EvidenceFingerprint) != 64 ||
		len(e.SettlementID) != 64 || strings.ToLower(e.EvidenceFingerprint) != e.EvidenceFingerprint ||
		strings.ToLower(e.SettlementID) != e.SettlementID || !approvedSource {
		return 0, ErrVideoSettlementInvalid
	}
	if e.ActualCostStatus != "actual" && e.ActualCostStatus != "zero_verified" {
		return 0, ErrVideoSettlementInvalid
	}
	micros, err := parseCNYMicrosExact(e.ActualCostCNYExact)
	if err != nil || (micros == 0 && e.ActualCostStatus != "zero_verified") || (micros > 0 && e.ActualCostStatus == "zero_verified") {
		return 0, ErrVideoSettlementInvalid
	}
	observed, err := time.Parse(time.RFC3339, e.ObservedAt)
	if err != nil || observed.After(time.Now().Add(5*time.Minute)) || observed.Before(time.Now().Add(-30*24*time.Hour)) {
		return 0, ErrVideoSettlementInvalid
	}
	if e.EvidenceFingerprint != expectedVideoEvidenceFingerprint(e) || e.SettlementID != expectedVideoSettlementID(e) {
		return 0, ErrVideoSettlementInvalid
	}
	return videoChargeQuota(micros)
}

func settlementOutcome(task *model.Task, applied, replay bool) *VideoSettlementOutcome {
	billing := task.PrivateData.BillingContext
	charged, refunded, supplemented, revision := task.Quota, 0, 0, 0
	status := "unavailable"
	if billing != nil {
		charged = billing.ChargedQuota
		refunded = billing.RefundedQuota
		supplemented = billing.SupplementedQuota
		revision = billing.SettlementRevision
		status = billing.BillingStatus
	}
	if charged == 0 && status != "refunded" {
		charged = task.Quota
	}
	return &VideoSettlementOutcome{
		TaskID:           task.TaskID,
		Revision:         revision,
		BillingStatus:    status,
		ChargedAmount:    quotaCNYExact(charged),
		RefundAmount:     quotaCNYExact(refunded),
		SupplementAmount: quotaCNYExact(supplemented),
		Applied:          applied,
		Replay:           replay,
	}
}

func ApplyVideoTaskSettlement(ctx context.Context, evidence VideoSettlementEvidence) (*VideoSettlementOutcome, error) {
	chargedQuota, err := validateVideoSettlementEvidence(evidence)
	if err != nil {
		return nil, err
	}
	var outcome *VideoSettlementOutcome
	var tokenKey string
	var userID int
	var settledTask model.Task
	var settledDelta int
	err = model.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var task model.Task
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("task_id = ?", evidence.JobID).First(&task).Error; err != nil {
			return err
		}
		billing := task.PrivateData.BillingContext
		userID = task.UserId
		if task.Status != model.TaskStatusSuccess || billing == nil || !constant.IsXingTuVideoContract(billing.ContractVersion) ||
			evidence.ContractVersion != billing.ContractVersion {
			return ErrVideoSettlementTaskNotReady
		}
		if task.GetUpstreamTaskID() != evidence.ProviderTaskID {
			return ErrVideoSettlementConflict
		}
		var existing model.VideoTaskSettlement
		existingResult := tx.Where("settlement_id = ? OR evidence_fingerprint = ?", evidence.SettlementID, evidence.EvidenceFingerprint).First(&existing)
		if existingResult.Error == nil {
			if existing.TaskRecordID != task.ID || existing.Revision != evidence.Revision ||
				existing.SettlementID != evidence.SettlementID || existing.EvidenceFingerprint != evidence.EvidenceFingerprint {
				return ErrVideoSettlementConflict
			}
			outcome = settlementOutcome(&task, false, true)
			return nil
		}
		if !errors.Is(existingResult.Error, gorm.ErrRecordNotFound) {
			return existingResult.Error
		}
		if evidence.Revision != billing.SettlementRevision+1 {
			return ErrVideoSettlementConflict
		}

		previousQuota := task.Quota
		delta := chargedQuota - previousQuota
		if task.PrivateData.TokenId > 0 && delta != 0 {
			var token model.Token
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ?", task.PrivateData.TokenId).First(&token).Error; err != nil {
				return err
			}
			tokenKey = token.Key
			if delta > 0 && !token.UnlimitedQuota {
				result := tx.Model(&model.Token{}).Where("id = ? AND remain_quota >= ?", token.Id, delta).
					Updates(map[string]any{"remain_quota": gorm.Expr("remain_quota - ?", delta), "used_quota": gorm.Expr("used_quota + ?", delta)})
				if result.Error != nil {
					return result.Error
				}
				if result.RowsAffected != 1 {
					billing.BillingStatus = "payment_required"
					task.UpdatedAt = time.Now().Unix()
					if updateErr := tx.Model(&model.Task{}).Where("id = ?", task.ID).
						Updates(map[string]any{"private_data": task.PrivateData, "updated_at": task.UpdatedAt}).Error; updateErr != nil {
						return updateErr
					}
					if enqueueErr := model.EnqueueXingTuVideoWebhookTx(tx, &task, model.XingTuWebhookBillingPaymentRequired,
						fmt.Sprintf("billing-payment-required:%s:%d", task.TaskID, evidence.Revision)); enqueueErr != nil {
						return enqueueErr
					}
					outcome = settlementOutcome(&task, false, false)
					return nil
				}
			} else if delta > 0 {
				result := tx.Model(&model.Token{}).Where("id = ?", token.Id).
					Updates(map[string]any{"remain_quota": gorm.Expr("remain_quota - ?", delta), "used_quota": gorm.Expr("used_quota + ?", delta)})
				if result.Error != nil || result.RowsAffected != 1 {
					if result.Error != nil {
						return result.Error
					}
					return gorm.ErrRecordNotFound
				}
			} else {
				refund := -delta
				result := tx.Model(&model.Token{}).Where("id = ?", token.Id).
					Updates(map[string]any{"remain_quota": gorm.Expr("remain_quota + ?", refund), "used_quota": gorm.Expr("used_quota - ?", refund)})
				if result.Error != nil || result.RowsAffected != 1 {
					if result.Error != nil {
						return result.Error
					}
					return gorm.ErrRecordNotFound
				}
			}
		}

		if taskIsSubscription(&task) {
			if delta > 0 {
				result := tx.Model(&model.UserSubscription{}).
					Where("id = ? AND (amount_total <= 0 OR amount_used + ? <= amount_total)", task.PrivateData.SubscriptionId, delta).
					Update("amount_used", gorm.Expr("amount_used + ?", delta))
				if result.Error != nil {
					return result.Error
				}
				if result.RowsAffected != 1 {
					return ErrVideoSettlementPaymentRequired
				}
			} else if delta < 0 {
				result := tx.Model(&model.UserSubscription{}).Where("id = ?", task.PrivateData.SubscriptionId).
					Update("amount_used", gorm.Expr("amount_used - ?", -delta))
				if result.Error != nil || result.RowsAffected != 1 {
					if result.Error != nil {
						return result.Error
					}
					return gorm.ErrRecordNotFound
				}
			}
		} else if delta != 0 {
			query := tx.Model(&model.User{}).Where("id = ?", task.UserId)
			if delta > 0 {
				query = query.Where("quota >= ?", delta)
			}
			result := query.
				Updates(map[string]any{
					"quota":      gorm.Expr("quota - ?", delta),
					"used_quota": gorm.Expr("used_quota + ?", delta),
				})
			if result.Error != nil || result.RowsAffected != 1 {
				if result.Error != nil {
					return result.Error
				}
				if delta > 0 {
					return ErrVideoSettlementPaymentRequired
				}
				return gorm.ErrRecordNotFound
			}
		}
		if taskIsSubscription(&task) && delta != 0 {
			result := tx.Model(&model.User{}).Where("id = ?", task.UserId).
				Update("used_quota", gorm.Expr("used_quota + ?", delta))
			if result.Error != nil || result.RowsAffected != 1 {
				if result.Error != nil {
					return result.Error
				}
				return gorm.ErrRecordNotFound
			}
		}
		if task.ChannelId > 0 && delta != 0 {
			if err := tx.Model(&model.Channel{}).Where("id = ?", task.ChannelId).
				Update("used_quota", gorm.Expr("used_quota + ?", delta)).Error; err != nil {
				return err
			}
		}

		settlement := model.VideoTaskSettlement{
			CreatedAt:            time.Now().Unix(),
			TaskRecordID:         task.ID,
			TaskID:               task.TaskID,
			SettlementID:         evidence.SettlementID,
			Revision:             evidence.Revision,
			EvidenceFingerprint:  evidence.EvidenceFingerprint,
			ProviderTaskID:       evidence.ProviderTaskID,
			ActualCostStatus:     evidence.ActualCostStatus,
			ActualCostCNYExact:   evidence.ActualCostCNYExact,
			EvidenceSource:       evidence.EvidenceSource,
			EvidenceID:           evidence.EvidenceID,
			ObservedAt:           evidence.ObservedAt,
			ChargedQuota:         chargedQuota,
			SettlementDeltaQuota: delta,
		}
		if err := tx.Create(&settlement).Error; err != nil {
			return err
		}
		if billing.ReservedQuota == 0 {
			billing.ReservedQuota = previousQuota
		}
		billing.ChargedQuota = chargedQuota
		billing.RefundedQuota = 0
		billing.SupplementedQuota = 0
		if chargedQuota < billing.ReservedQuota {
			billing.RefundedQuota = billing.ReservedQuota - chargedQuota
		} else if chargedQuota > billing.ReservedQuota {
			billing.SupplementedQuota = chargedQuota - billing.ReservedQuota
		}
		billing.SettlementID = evidence.SettlementID
		billing.SettlementRevision = evidence.Revision
		billing.SettlementFingerprint = evidence.EvidenceFingerprint
		billing.BillingStatus = "settled"
		task.Quota = chargedQuota
		task.UpdatedAt = time.Now().Unix()
		if err := tx.Model(&model.Task{}).Where("id = ?", task.ID).
			Updates(map[string]any{"quota": chargedQuota, "private_data": task.PrivateData, "updated_at": task.UpdatedAt}).Error; err != nil {
			return err
		}
		if err := model.EnqueueXingTuVideoWebhookTx(tx, &task, model.XingTuWebhookBillingSettled,
			"billing-settled:"+task.TaskID+":"+evidence.SettlementID); err != nil {
			return err
		}
		outcome = settlementOutcome(&task, true, false)
		settledTask = task
		settledDelta = delta
		return nil
	})
	if err != nil {
		if errors.Is(err, ErrVideoSettlementPaymentRequired) {
			task, markErr := markVideoSettlementPaymentRequired(ctx, evidence.JobID, evidence.Revision)
			if markErr != nil {
				return nil, markErr
			}
			return settlementOutcome(task, false, false), nil
		}
		return nil, err
	}
	if userID > 0 {
		_ = model.InvalidateUserCache(userID)
	}
	if tokenKey != "" {
		_ = model.InvalidateTokenCache(tokenKey)
	}
	if outcome != nil && outcome.Applied && settledDelta != 0 {
		recordVideoSettlementDelta(&settledTask, settledDelta, outcome.Revision)
	}
	return outcome, nil
}

func markVideoSettlementPaymentRequired(ctx context.Context, taskID string, revision int) (*model.Task, error) {
	var task model.Task
	err := model.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("task_id = ?", taskID).First(&task).Error; err != nil {
			return err
		}
		billing := task.PrivateData.BillingContext
		if task.Status != model.TaskStatusSuccess || billing == nil || !constant.IsXingTuVideoContract(billing.ContractVersion) {
			return ErrVideoSettlementTaskNotReady
		}
		if billing.SettlementRevision+1 != revision {
			return nil
		}
		billing.BillingStatus = "payment_required"
		task.UpdatedAt = time.Now().Unix()
		if err := tx.Model(&model.Task{}).Where("id = ?", task.ID).
			Updates(map[string]any{"private_data": task.PrivateData, "updated_at": task.UpdatedAt}).Error; err != nil {
			return err
		}
		return model.EnqueueXingTuVideoWebhookTx(tx, &task, model.XingTuWebhookBillingPaymentRequired,
			fmt.Sprintf("billing-payment-required:%s:%d", task.TaskID, revision))
	})
	return &task, err
}

// RefundVideoTaskReservation atomically releases a failed v2 video's frozen
// reservation. The settlement row makes retries and concurrent refund workers
// idempotent across process restarts.
func RefundVideoTaskReservation(ctx context.Context, taskID string) (*VideoSettlementOutcome, error) {
	if !strings.HasPrefix(taskID, "task_") || len(taskID) > 191 {
		return nil, ErrVideoSettlementInvalid
	}
	var outcome *VideoSettlementOutcome
	var tokenKey string
	var userID int
	var refundedTask model.Task
	var refundedQuota int
	err := model.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var task model.Task
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("task_id = ?", taskID).First(&task).Error; err != nil {
			return err
		}
		billing := task.PrivateData.BillingContext
		userID = task.UserId
		if task.Status != model.TaskStatusFailure || billing == nil || !constant.IsXingTuVideoContract(billing.ContractVersion) {
			return ErrVideoSettlementTaskNotReady
		}
		if billing.BillingStatus == "refunded" {
			outcome = settlementOutcome(&task, false, true)
			return nil
		}
		if billing.BillingStatus != "refund_pending" && billing.BillingStatus != "reserved" {
			return ErrVideoSettlementConflict
		}

		refundQuota := billing.ReservedQuota
		if refundQuota <= 0 {
			refundQuota = task.Quota
		}
		if refundQuota < 0 {
			return ErrVideoSettlementInvalid
		}
		if task.PrivateData.TokenId > 0 && refundQuota > 0 {
			var token model.Token
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
				Where("id = ?", task.PrivateData.TokenId).First(&token).Error; err != nil {
				return err
			}
			tokenKey = token.Key
			result := tx.Model(&model.Token{}).Where("id = ?", token.Id).
				Updates(map[string]any{
					"remain_quota": gorm.Expr("remain_quota + ?", refundQuota),
					"used_quota":   gorm.Expr("used_quota - ?", refundQuota),
				})
			if result.Error != nil || result.RowsAffected != 1 {
				if result.Error != nil {
					return result.Error
				}
				return gorm.ErrRecordNotFound
			}
		}
		if refundQuota > 0 {
			if taskIsSubscription(&task) {
				var subscription model.UserSubscription
				if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
					Where("id = ?", task.PrivateData.SubscriptionId).First(&subscription).Error; err != nil {
					return err
				}
				used := subscription.AmountUsed - int64(refundQuota)
				if used < 0 {
					used = 0
				}
				if err := tx.Model(&model.UserSubscription{}).Where("id = ?", subscription.Id).
					Update("amount_used", used).Error; err != nil {
					return err
				}
			} else {
				result := tx.Model(&model.User{}).Where("id = ?", task.UserId).
					Updates(map[string]any{
						"quota":      gorm.Expr("quota + ?", refundQuota),
						"used_quota": gorm.Expr("used_quota - ?", refundQuota),
					})
				if result.Error != nil || result.RowsAffected != 1 {
					if result.Error != nil {
						return result.Error
					}
					return gorm.ErrRecordNotFound
				}
			}
		}
		if taskIsSubscription(&task) && refundQuota > 0 {
			result := tx.Model(&model.User{}).Where("id = ?", task.UserId).
				Update("used_quota", gorm.Expr("used_quota - ?", refundQuota))
			if result.Error != nil || result.RowsAffected != 1 {
				if result.Error != nil {
					return result.Error
				}
				return gorm.ErrRecordNotFound
			}
		}
		if task.ChannelId > 0 && refundQuota > 0 {
			if err := tx.Model(&model.Channel{}).Where("id = ?", task.ChannelId).
				Update("used_quota", gorm.Expr("used_quota - ?", refundQuota)).Error; err != nil {
				return err
			}
		}

		revision := billing.SettlementRevision + 1
		fingerprint := settlementDigest(
			"xtai-video-failure-refund-v1",
			task.TaskID,
			task.GetUpstreamTaskID(),
			strconv.Itoa(refundQuota),
		)
		settlementID := settlementDigest(
			"xtai-video-settlement-v2",
			task.TaskID,
			strconv.Itoa(revision),
			fingerprint,
		)
		settlement := model.VideoTaskSettlement{
			CreatedAt:            time.Now().Unix(),
			TaskRecordID:         task.ID,
			TaskID:               task.TaskID,
			SettlementID:         settlementID,
			Revision:             revision,
			EvidenceFingerprint:  fingerprint,
			ProviderTaskID:       task.GetUpstreamTaskID(),
			ActualCostStatus:     "provider_failure",
			ActualCostCNYExact:   "0.000000",
			EvidenceSource:       "newapi_terminal_task",
			EvidenceID:           fingerprint,
			ObservedAt:           time.Now().UTC().Format(time.RFC3339),
			ChargedQuota:         0,
			SettlementDeltaQuota: -refundQuota,
		}
		if err := tx.Create(&settlement).Error; err != nil {
			return err
		}
		billing.ChargedQuota = 0
		billing.RefundedQuota = refundQuota
		billing.SettlementID = settlementID
		billing.SettlementRevision = revision
		billing.SettlementFingerprint = fingerprint
		billing.BillingStatus = "refunded"
		task.UpdatedAt = time.Now().Unix()
		if err := tx.Model(&model.Task{}).Where("id = ?", task.ID).
			Updates(map[string]any{"private_data": task.PrivateData, "updated_at": task.UpdatedAt}).Error; err != nil {
			return err
		}
		if err := model.EnqueueXingTuVideoWebhookTx(tx, &task, model.XingTuWebhookTaskFailed,
			"task-failed:"+task.TaskID+":"+settlementID); err != nil {
			return err
		}
		outcome = settlementOutcome(&task, true, false)
		refundedTask = task
		refundedQuota = refundQuota
		return nil
	})
	if err != nil {
		return nil, err
	}
	if userID > 0 {
		_ = model.InvalidateUserCache(userID)
	}
	if tokenKey != "" {
		_ = model.InvalidateTokenCache(tokenKey)
	}
	if outcome != nil && outcome.Applied && refundedQuota > 0 {
		recordVideoSettlementDelta(&refundedTask, -refundedQuota, outcome.Revision)
	}
	return outcome, nil
}

func recordVideoSettlementDelta(task *model.Task, delta, revision int) {
	if task == nil || delta == 0 {
		return
	}
	logType := model.LogTypeConsume
	quota := delta
	if delta < 0 {
		logType = model.LogTypeRefund
		quota = -delta
	}
	model.RecordTaskBillingLog(model.RecordTaskBillingLogParams{
		UserId:    task.UserId,
		LogType:   logType,
		ChannelId: task.ChannelId,
		ModelName: taskModelName(task),
		Quota:     quota,
		TokenId:   task.PrivateData.TokenId,
		Group:     task.Group,
		Other: map[string]interface{}{
			"task_id":                  task.TaskID,
			"billing_contract_version": VideoBillingContractVersion,
			"settlement_revision":      revision,
		},
	})
}
