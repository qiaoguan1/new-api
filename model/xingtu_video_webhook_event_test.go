package model

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestEnqueueXingTuVideoWebhookIsIdempotentAndPublic(t *testing.T) {
	truncateTables(t)
	task := &Task{
		TaskID:     "task_webhook_public_001",
		Status:     TaskStatusSuccess,
		Progress:   "100%",
		Properties: Properties{OriginModelName: "seedance-2.0"},
		PrivateData: TaskPrivateData{
			RequestID:      "req_webhook_public_001",
			UpstreamTaskID: "must-not-leak-provider-task",
			ResultURL:      "https://provider.invalid/private.mp4",
			BillingContext: &TaskBillingContext{
				ContractVersion: "xtai-video-billing-v2",
				BillingStatus:   "settled",
				ReservedQuota:   750000,
				QuotaPerUnit:    500000,
			},
		},
	}
	insertTask(t, task)

	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		if err := EnqueueXingTuVideoWebhookTx(tx, task, XingTuWebhookBillingSettled, "billing-settled:"+task.TaskID); err != nil {
			return err
		}
		return EnqueueXingTuVideoWebhookTx(tx, task, XingTuWebhookBillingSettled, "billing-settled:"+task.TaskID)
	}))

	var events []XingTuVideoWebhookEvent
	require.NoError(t, DB.Find(&events).Error)
	require.Len(t, events, 1)
	assert.NotEmpty(t, events[0].EventID)
	assert.Len(t, events[0].EventKey, 64)
	assert.Equal(t, XingTuWebhookStatusPending, events[0].Status)
	assert.NotContains(t, string(events[0].Payload), "must-not-leak")
	assert.NotContains(t, string(events[0].Payload), "provider.invalid")
	var envelope map[string]any
	require.NoError(t, json.Unmarshal(events[0].Payload, &envelope))
	assert.Equal(t, float64(1), envelope["event_version"])
	assert.Contains(t, envelope["occurred_at"], "+08:00")
}

func TestWebhookRetryScheduleAndDeadLetter(t *testing.T) {
	assert.Equal(t, 10*time.Second, XingTuWebhookRetryDelay(1))
	assert.Equal(t, 30*time.Second, XingTuWebhookRetryDelay(2))
	assert.Equal(t, time.Hour, XingTuWebhookRetryDelay(9))
	assert.True(t, XingTuWebhookShouldDeadLetter(XingTuWebhookMaxAttempts))
}

func TestTaskSuccessCASAndWebhookCommitTogetherOnce(t *testing.T) {
	truncateTables(t)
	task := &Task{
		TaskID: "task_success_event_001", Status: TaskStatusInProgress, Progress: "50%",
		PrivateData: TaskPrivateData{RequestID: "req_success_event_001", BillingContext: &TaskBillingContext{
			ContractVersion: "xtai-video-billing-v2", BillingStatus: "settlement_pending", QuotaPerUnit: 500000,
		}},
	}
	insertTask(t, task)
	task.Status = TaskStatusSuccess
	task.Progress = "100%"
	won, err := task.UpdateWithStatusAndXingTuWebhook(TaskStatusInProgress, XingTuWebhookTaskSucceeded, "task-success:"+task.TaskID)
	require.NoError(t, err)
	assert.True(t, won)
	won, err = task.UpdateWithStatusAndXingTuWebhook(TaskStatusInProgress, XingTuWebhookTaskSucceeded, "task-success:"+task.TaskID)
	require.NoError(t, err)
	assert.False(t, won)
	var count int64
	require.NoError(t, DB.Model(&XingTuVideoWebhookEvent{}).Count(&count).Error)
	assert.Equal(t, int64(1), count)
}

func TestExpiredDeliveryLeaseIsClaimedAgainAfterRestart(t *testing.T) {
	truncateTables(t)
	event := XingTuVideoWebhookEvent{
		CreatedAt: time.Now().Unix(), UpdatedAt: time.Now().Unix(), EventID: "evt_restart",
		EventKey: "restart:key", TaskID: "task_restart", EventType: XingTuWebhookTaskSucceeded,
		ContractVersion: "xtai-video-billing-v2", Payload: []byte(`{"event_id":"evt_restart"}`),
		Status: XingTuWebhookStatusDelivering, Attempts: 1, NextAttemptAt: time.Now().Add(-time.Minute).Unix(),
	}
	require.NoError(t, DB.Create(&event).Error)
	claimed, err := ClaimDueXingTuVideoWebhook(time.Now(), time.Minute)
	require.NoError(t, err)
	require.NotNil(t, claimed)
	assert.Equal(t, 2, claimed.Attempts)
}

func TestWebhookFailureMovesToDeadLetterAtAttemptLimit(t *testing.T) {
	truncateTables(t)
	now := time.Now()
	event := XingTuVideoWebhookEvent{
		CreatedAt: now.Unix(), UpdatedAt: now.Unix(), EventID: "evt_dead_letter",
		EventKey: "dead-letter:key", TaskID: "task_dead_letter", EventType: XingTuWebhookTaskSucceeded,
		ContractVersion: "xtai-video-billing-v2", Payload: []byte(`{"event_id":"evt_dead_letter"}`),
		Status: XingTuWebhookStatusDelivering, Attempts: XingTuWebhookMaxAttempts, NextAttemptAt: now.Add(time.Minute).Unix(),
	}
	require.NoError(t, DB.Create(&event).Error)
	require.NoError(t, CompleteXingTuVideoWebhook(event.ID, XingTuWebhookMaxAttempts, false, 503, "non_2xx", now))
	var updated XingTuVideoWebhookEvent
	require.NoError(t, DB.First(&updated, event.ID).Error)
	assert.Equal(t, XingTuWebhookStatusDeadLetter, updated.Status)
	assert.Equal(t, "non_2xx", updated.LastErrorCode)
}

func TestWebhookFailureSchedulesDurableRetry(t *testing.T) {
	truncateTables(t)
	now := time.Now()
	event := XingTuVideoWebhookEvent{
		CreatedAt: now.Unix(), UpdatedAt: now.Unix(), EventID: "evt_retry",
		EventKey: "retry:key", TaskID: "task_retry", EventType: XingTuWebhookTaskSucceeded,
		ContractVersion: "xtai-video-billing-v2", Payload: []byte(`{"event_id":"evt_retry"}`),
		Status: XingTuWebhookStatusDelivering, Attempts: 1, NextAttemptAt: now.Add(time.Minute).Unix(),
	}
	require.NoError(t, DB.Create(&event).Error)
	require.NoError(t, CompleteXingTuVideoWebhook(event.ID, 1, false, 503, "non_2xx", now))
	var updated XingTuVideoWebhookEvent
	require.NoError(t, DB.First(&updated, event.ID).Error)
	assert.Equal(t, XingTuWebhookStatusPending, updated.Status)
	assert.Equal(t, now.Add(10*time.Second).Unix(), updated.NextAttemptAt)
}
