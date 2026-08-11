package model

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	XingTuWebhookTaskSucceeded          = "video.task.succeeded"
	XingTuWebhookBillingSettled         = "video.billing.settled"
	XingTuWebhookTaskFailed             = "video.task.failed"
	XingTuWebhookBillingPaymentRequired = "video.billing.payment_required"
	XingTuWebhookBillingPendingReview   = "video.billing.pending_review"

	XingTuWebhookStatusPending    = "pending"
	XingTuWebhookStatusDelivering = "delivering"
	XingTuWebhookStatusDelivered  = "delivered"
	XingTuWebhookStatusDeadLetter = "dead_letter"

	XingTuWebhookMaxAttempts = 30
	xingTuWebhookMaxPayload  = 64 * 1024
)

// XingTuVideoWebhookEvent is a durable public-data-only outbox record.
type XingTuVideoWebhookEvent struct {
	ID              int64  `json:"-" gorm:"primaryKey;autoIncrement"`
	CreatedAt       int64  `json:"-" gorm:"index"`
	UpdatedAt       int64  `json:"-"`
	EventID         string `json:"-" gorm:"type:varchar(80);uniqueIndex"`
	EventKey        string `json:"-" gorm:"type:varchar(191);uniqueIndex"`
	TaskID          string `json:"-" gorm:"type:varchar(191);index"`
	EventType       string `json:"-" gorm:"type:varchar(64);index"`
	ContractVersion string `json:"-" gorm:"type:varchar(64)"`
	Payload         []byte `json:"-" gorm:"type:text"`
	Status          string `json:"-" gorm:"type:varchar(24);index:idx_xingtu_webhook_due,priority:1"`
	Attempts        int    `json:"-"`
	NextAttemptAt   int64  `json:"-" gorm:"index:idx_xingtu_webhook_due,priority:2"`
	LastAttemptAt   int64  `json:"-"`
	DeliveredAt     int64  `json:"-"`
	LastHTTPStatus  int    `json:"-"`
	LastErrorCode   string `json:"-" gorm:"type:varchar(32)"`
}

func XingTuWebhookRetryDelay(attempt int) time.Duration {
	schedule := []time.Duration{10 * time.Second, 30 * time.Second, time.Minute, 2 * time.Minute, 5 * time.Minute, 10 * time.Minute, 30 * time.Minute}
	if attempt <= 0 {
		return 0
	}
	if attempt <= len(schedule) {
		return schedule[attempt-1]
	}
	return time.Hour
}

func XingTuWebhookShouldDeadLetter(attempts int) bool { return attempts >= XingTuWebhookMaxAttempts }

func xingTuWebhookEventID(eventKey string) string {
	sum := sha256.Sum256([]byte("xtai-video-webhook-v1\x00" + eventKey))
	return "evt_" + hex.EncodeToString(sum[:16])
}

func xingTuWebhookEventKey(eventKey string) string {
	sum := sha256.Sum256([]byte(eventKey))
	return hex.EncodeToString(sum[:])
}

func validXingTuWebhookEventType(eventType string) bool {
	switch eventType {
	case XingTuWebhookTaskSucceeded, XingTuWebhookBillingSettled, XingTuWebhookTaskFailed,
		XingTuWebhookBillingPaymentRequired, XingTuWebhookBillingPendingReview:
		return true
	default:
		return false
	}
}

// EnqueueXingTuVideoWebhookTx stores one immutable callback snapshot in the
// caller's transaction. Repeating eventKey is a successful no-op.
func EnqueueXingTuVideoWebhookTx(tx *gorm.DB, task *Task, eventType, eventKey string) error {
	if tx == nil || task == nil || task.TaskID == "" || eventKey == "" || !validXingTuWebhookEventType(eventType) ||
		task.PrivateData.BillingContext == nil || !constant.IsXingTuVideoContract(task.PrivateData.BillingContext.ContractVersion) {
		return errors.New("invalid xingtu webhook event")
	}
	beijing := time.FixedZone("Asia/Shanghai", 8*60*60)
	now := time.Now().In(beijing)
	if task.UpdatedAt > 0 {
		now = time.Unix(task.UpdatedAt, 0).In(beijing)
	}
	eventID := xingTuWebhookEventID(eventKey)
	payload, err := json.Marshal(dto.XingTuVideoWebhookEnvelope{
		EventID: eventID, EventVersion: 1, EventType: eventType,
		OccurredAt: now.Format(time.RFC3339), Data: task.ToXingTuVideo(),
	})
	if err != nil {
		return err
	}
	if len(payload) > xingTuWebhookMaxPayload {
		return fmt.Errorf("xingtu webhook payload exceeds %d bytes", xingTuWebhookMaxPayload)
	}
	event := XingTuVideoWebhookEvent{
		CreatedAt: time.Now().Unix(), UpdatedAt: time.Now().Unix(), EventID: eventID,
		EventKey: xingTuWebhookEventKey(eventKey), TaskID: task.TaskID, EventType: eventType,
		ContractVersion: task.PrivateData.BillingContext.ContractVersion, Payload: payload,
		Status: XingTuWebhookStatusPending, NextAttemptAt: time.Now().Unix(),
	}
	return tx.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "event_key"}}, DoNothing: true}).Create(&event).Error
}

// ClaimDueXingTuVideoWebhook atomically leases one due event for delivery.
func ClaimDueXingTuVideoWebhook(now time.Time, lease time.Duration) (*XingTuVideoWebhookEvent, error) {
	var claimed *XingTuVideoWebhookEvent
	err := DB.Transaction(func(tx *gorm.DB) error {
		var event XingTuVideoWebhookEvent
		err := tx.Where("status IN ? AND next_attempt_at <= ?", []string{XingTuWebhookStatusPending, XingTuWebhookStatusDelivering}, now.Unix()).Order("id").First(&event).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		result := tx.Model(&XingTuVideoWebhookEvent{}).
			Where("id = ? AND status = ? AND next_attempt_at <= ?", event.ID, event.Status, now.Unix()).
			Updates(map[string]any{"status": XingTuWebhookStatusDelivering, "attempts": gorm.Expr("attempts + 1"), "last_attempt_at": now.Unix(), "next_attempt_at": now.Add(lease).Unix(), "updated_at": now.Unix()})
		if result.Error != nil || result.RowsAffected != 1 {
			return result.Error
		}
		event.Status = XingTuWebhookStatusDelivering
		event.Attempts++
		claimed = &event
		return nil
	})
	return claimed, err
}

func CompleteXingTuVideoWebhook(id int64, expectedAttempts int, delivered bool, httpStatus int, errorCode string, now time.Time) error {
	updates := map[string]any{"updated_at": now.Unix(), "last_http_status": httpStatus, "last_error_code": errorCode}
	if delivered {
		updates["status"] = XingTuWebhookStatusDelivered
		updates["delivered_at"] = now.Unix()
	} else {
		var event XingTuVideoWebhookEvent
		if err := DB.Select("attempts").First(&event, id).Error; err != nil {
			return err
		}
		if XingTuWebhookShouldDeadLetter(event.Attempts) {
			updates["status"] = XingTuWebhookStatusDeadLetter
		} else {
			updates["status"] = XingTuWebhookStatusPending
			updates["next_attempt_at"] = now.Add(XingTuWebhookRetryDelay(event.Attempts)).Unix()
		}
	}
	result := DB.Model(&XingTuVideoWebhookEvent{}).
		Where("id = ? AND status = ? AND attempts = ?", id, XingTuWebhookStatusDelivering, expectedAttempts).
		Updates(updates)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("xingtu webhook delivery lease was lost")
	}
	return nil
}
