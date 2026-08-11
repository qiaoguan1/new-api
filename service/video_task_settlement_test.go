package service

import (
	"context"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func settlementEvidence(taskID, upstreamTaskID, cost string, revision int) VideoSettlementEvidence {
	e := VideoSettlementEvidence{
		ContractVersion:    VideoBillingContractVersion,
		JobID:              taskID,
		Revision:           revision,
		ProviderTaskID:     upstreamTaskID,
		ActualCostStatus:   "actual",
		ActualCostCNYExact: cost,
		EvidenceSource:     "provider_account_ledger",
		EvidenceID:         "ledger-row-1",
		ObservedAt:         time.Now().UTC().Format(time.RFC3339),
	}
	if cost == "0.000000" {
		e.ActualCostStatus = "zero_verified"
	}
	e.EvidenceFingerprint = expectedVideoEvidenceFingerprint(e)
	e.SettlementID = expectedVideoSettlementID(e)
	return e
}

func seedSettlingVideoTask(t *testing.T, userID, tokenID, reservedQuota int, unlimited bool) *model.Task {
	t.Helper()
	seedUser(t, userID, 100)
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", userID).
		Update("used_quota", reservedQuota).Error)
	seedToken(t, tokenID, userID, "sk-settlement", 2_000_000)
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", tokenID).
		Updates(map[string]any{
			"unlimited_quota": unlimited,
			"remain_quota":    2_000_000 - reservedQuota,
			"used_quota":      reservedQuota,
		}).Error)
	task := makeTask(userID, 1, reservedQuota, tokenID, BillingSourceWallet, 0)
	task.TaskID = "task_video_settlement"
	task.Status = model.TaskStatusSuccess
	task.PrivateData.UpstreamTaskID = "provider-task-1"
	task.PrivateData.BillingContext.ContractVersion = VideoBillingContractVersion
	task.PrivateData.BillingContext.BillingStatus = "settlement_pending"
	task.PrivateData.BillingContext.ReservedQuota = reservedQuota
	task.PrivateData.BillingContext.QuotaPerUnit = 500000
	require.NoError(t, task.Insert())
	return task
}

func TestApplyVideoTaskSettlementSupplementsWalletAndIsReplaySafe(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 201, 201, 500000, true)
	evidence := settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "1.450000", 1)

	first, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.NoError(t, err)
	require.True(t, first.Applied)
	assert.Equal(t, "settled_with_debt", first.BillingStatus)
	assert.Equal(t, "2.175000", first.ChargedAmount)
	assert.Equal(t, "1.175000", first.SupplementAmount)
	assert.Equal(t, -587400, getUserQuota(t, 201))
	assert.Equal(t, 1_087_500, getUserUsedQuota(t, 201))

	replay, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.NoError(t, err)
	assert.True(t, replay.Replay)
	assert.False(t, replay.Applied)
	assert.Equal(t, -587400, getUserQuota(t, 201))
	assert.Equal(t, int64(1), countVideoSettlements(t))
	assert.Equal(t, int64(1), countVideoWebhookEvents(t, model.XingTuWebhookBillingSettled))
}

func TestApplyVideoTaskSettlementRefundsReservation(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 202, 202, 500000, true)
	evidence := settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "0.200000", 1)

	outcome, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.NoError(t, err)
	assert.Equal(t, "settled", outcome.BillingStatus)
	assert.Equal(t, "0.300000", outcome.ChargedAmount)
	assert.Equal(t, "0.700000", outcome.RefundAmount)
	assert.Equal(t, 350100, getUserQuota(t, 202))
	assert.Equal(t, 150000, getUserUsedQuota(t, 202))
}

func TestLaterSettlementRevisionReportsNetDifferenceFromReservation(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 207, 207, 500000, true)
	first, err := ApplyVideoTaskSettlement(
		context.Background(),
		settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "0.200000", 1),
	)
	require.NoError(t, err)
	assert.Equal(t, "0.700000", first.RefundAmount)

	second, err := ApplyVideoTaskSettlement(
		context.Background(),
		settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "0.400000", 2),
	)
	require.NoError(t, err)
	assert.Equal(t, "0.600000", second.ChargedAmount)
	assert.Equal(t, "0.400000", second.RefundAmount)
	assert.Equal(t, "0.000000", second.SupplementAmount)
	assert.Equal(t, 200100, getUserQuota(t, 207))
	assert.Equal(t, 300000, getUserUsedQuota(t, 207))
	assert.Equal(t, int64(2), countVideoSettlements(t))
}

func TestApplyVideoTaskSettlementRejectsWrongProviderTask(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 203, 203, 500000, true)
	evidence := settlementEvidence(task.TaskID, "different-provider-task", "0.200000", 1)

	_, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.ErrorIs(t, err, ErrVideoSettlementConflict)
	assert.Equal(t, 100, getUserQuota(t, 203))
	assert.Equal(t, int64(0), countVideoSettlements(t))
}

func TestSuccessfulVideoTaskWaitsForExactSettlementEvidence(t *testing.T) {
	task := &model.Task{
		Status: model.TaskStatusSuccess,
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{
			ContractVersion: VideoBillingContractVersion,
			BillingStatus:   "reserved",
		}},
	}
	captureTaskUsage(task, &relaycommon.TaskInfo{TotalTokens: 123})
	assert.Equal(t, "settlement_pending", task.PrivateData.BillingContext.BillingStatus)
	assert.Equal(t, 123, task.PrivateData.BillingContext.TotalTokens)
}

func TestConcurrentVideoSettlementAppliesMoneyOnce(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 204, 204, 500000, true)
	evidence := settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "1.450000", 1)

	var wg sync.WaitGroup
	errs := make(chan error, 5)
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := ApplyVideoTaskSettlement(context.Background(), evidence)
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		require.NoError(t, err)
	}
	assert.Equal(t, -587400, getUserQuota(t, 204))
	assert.Equal(t, int64(1), countVideoSettlements(t))
}

func TestFiniteTokenSettlementWaitsForRecharge(t *testing.T) {
	truncate(t)
	task := seedSettlingVideoTask(t, 205, 205, 500000, false)
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", 205).Update("remain_quota", 100).Error)
	evidence := settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "1.450000", 1)

	pending, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.NoError(t, err)
	assert.Equal(t, "payment_required", pending.BillingStatus)
	assert.Equal(t, 100, getUserQuota(t, 205))
	assert.Equal(t, 100, getTokenRemainQuota(t, 205))
	assert.Equal(t, int64(0), countVideoSettlements(t))
	assert.Equal(t, int64(1), countVideoWebhookEvents(t, model.XingTuWebhookBillingPaymentRequired))

	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", 205).Update("remain_quota", 600000).Error)
	settled, err := ApplyVideoTaskSettlement(context.Background(), evidence)
	require.NoError(t, err)
	assert.True(t, settled.Applied)
	assert.Equal(t, int64(1), countVideoSettlements(t))
}

func TestSubscriptionSettlementNeverOverdraftsAndKeepsReservation(t *testing.T) {
	truncate(t)
	seedUser(t, 208, 100)
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", 208).
		Update("used_quota", 500000).Error)
	seedToken(t, 208, 208, "sk-subscription-settlement", 1_500_000)
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", 208).
		Update("used_quota", 500000).Error)
	seedSubscription(t, 208, 208, 500000, 500000)
	task := makeTask(208, 1, 500000, 208, BillingSourceSubscription, 208)
	task.TaskID = "task_subscription_payment_required"
	task.Status = model.TaskStatusSuccess
	task.PrivateData.UpstreamTaskID = "provider-subscription-1"
	task.PrivateData.BillingContext.ContractVersion = VideoBillingContractVersion
	task.PrivateData.BillingContext.BillingStatus = "settlement_pending"
	task.PrivateData.BillingContext.ReservedQuota = 500000
	task.PrivateData.BillingContext.QuotaPerUnit = 500000
	require.NoError(t, task.Insert())

	outcome, err := ApplyVideoTaskSettlement(
		context.Background(),
		settlementEvidence(task.TaskID, task.GetUpstreamTaskID(), "1.450000", 1),
	)
	require.NoError(t, err)
	assert.Equal(t, "payment_required", outcome.BillingStatus)
	assert.Equal(t, int64(0), countVideoSettlements(t))
	assert.Equal(t, 1_500_000, getTokenRemainQuota(t, 208))
	assert.Equal(t, 500000, getUserUsedQuota(t, 208))
	var subscription model.UserSubscription
	require.NoError(t, model.DB.First(&subscription, 208).Error)
	assert.Equal(t, int64(500000), subscription.AmountUsed)
}

func TestVideoSettlementRejectsUnapprovedOrStaleEvidence(t *testing.T) {
	taskID := "task_evidence_validation"
	evidence := settlementEvidence(taskID, "provider-task-1", "0.200000", 1)
	evidence.EvidenceSource = "provider_catalog_price"
	evidence.EvidenceFingerprint = expectedVideoEvidenceFingerprint(evidence)
	evidence.SettlementID = expectedVideoSettlementID(evidence)
	_, err := validateVideoSettlementEvidence(evidence)
	require.ErrorIs(t, err, ErrVideoSettlementInvalid)

	evidence = settlementEvidence(taskID, "provider-task-1", "0.200000", 1)
	evidence.ObservedAt = time.Now().Add(-31 * 24 * time.Hour).UTC().Format(time.RFC3339)
	evidence.EvidenceFingerprint = expectedVideoEvidenceFingerprint(evidence)
	evidence.SettlementID = expectedVideoSettlementID(evidence)
	_, err = validateVideoSettlementEvidence(evidence)
	require.ErrorIs(t, err, ErrVideoSettlementInvalid)
}

func TestPendingVideoSettlementScanDoesNotMissOlderTask(t *testing.T) {
	truncate(t)
	pending := &model.Task{
		TaskID: "task_pending_older",
		Status: model.TaskStatusSuccess,
		PrivateData: model.TaskPrivateData{
			UpstreamTaskID: "provider-pending-older",
			BillingContext: &model.TaskBillingContext{
				ContractVersion: VideoBillingContractVersion,
				BillingStatus:   "settlement_pending",
			},
		},
	}
	require.NoError(t, pending.Insert())
	for index := 0; index < 3; index++ {
		require.NoError(t, (&model.Task{
			TaskID: "task_newer_non_pending_" + strconv.Itoa(index),
			Status: model.TaskStatusSuccess,
		}).Insert())
	}

	items, err := ListPendingVideoSettlementTasks(1)
	require.NoError(t, err)
	require.Len(t, items, 1)
	assert.Equal(t, pending.TaskID, items[0].JobID)
	assert.Equal(t, "provider-pending-older", items[0].ProviderTaskID)
}

func TestFailedVideoReservationRefundIsAtomicAndReplaySafe(t *testing.T) {
	truncate(t)
	seedUser(t, 206, -499900)
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", 206).
		Update("used_quota", 500000).Error)
	seedToken(t, 206, 206, "sk-failed-refund", 1_500_000)
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", 206).
		Update("used_quota", 500000).Error)
	task := makeTask(206, 1, 500000, 206, BillingSourceWallet, 0)
	task.TaskID = "task_failed_video_refund"
	task.Status = model.TaskStatusFailure
	task.PrivateData.UpstreamTaskID = "provider-failed-1"
	task.PrivateData.BillingContext.ContractVersion = VideoBillingContractVersion
	// A timeout sweeper can persist FAILURE while the last durable billing state
	// is still reserved; the atomic refund must recover that state as well.
	task.PrivateData.BillingContext.BillingStatus = "reserved"
	task.PrivateData.BillingContext.ReservedQuota = 500000
	require.NoError(t, task.Insert())

	var wg sync.WaitGroup
	errs := make(chan error, 5)
	for index := 0; index < 5; index++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := RefundVideoTaskReservation(context.Background(), task.TaskID)
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		require.NoError(t, err)
	}

	assert.Equal(t, 100, getUserQuota(t, 206))
	assert.Equal(t, 0, getUserUsedQuota(t, 206))
	assert.Equal(t, 2_000_000, getTokenRemainQuota(t, 206))
	assert.Equal(t, int64(1), countVideoSettlements(t))
	var updated model.Task
	require.NoError(t, model.DB.Where("task_id = ?", task.TaskID).First(&updated).Error)
	require.NotNil(t, updated.PrivateData.BillingContext)
	assert.Equal(t, "refunded", updated.PrivateData.BillingContext.BillingStatus)
	assert.Equal(t, 500000, updated.PrivateData.BillingContext.RefundedQuota)
	assert.Equal(t, int64(1), countVideoWebhookEvents(t, model.XingTuWebhookTaskFailed))
}

func getUserUsedQuota(t *testing.T, userID int) int {
	t.Helper()
	var used int
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", userID).
		Select("used_quota").Scan(&used).Error)
	return used
}

func countVideoSettlements(t *testing.T) int64 {
	t.Helper()
	var count int64
	require.NoError(t, model.DB.Model(&model.VideoTaskSettlement{}).Count(&count).Error)
	return count
}

func countVideoWebhookEvents(t *testing.T, eventType string) int64 {
	t.Helper()
	var count int64
	require.NoError(t, model.DB.Model(&model.XingTuVideoWebhookEvent{}).Where("event_type = ?", eventType).Count(&count).Error)
	return count
}
