package model

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestTaskVideoUsageSettledExposesTokensAndCNYCharge(t *testing.T) {
	task := &Task{
		Quota:  745200,
		Status: TaskStatusSuccess,
		PrivateData: TaskPrivateData{BillingContext: &TaskBillingContext{
			QuotaPerUnit:     500000,
			BillingCurrency:  "CNY",
			BillingStatus:    "settled",
			CompletionTokens: 86400,
			TotalTokens:      86400,
		}},
	}

	usage := task.VideoUsage()

	require.Equal(t, 86400, usage.OutputTokens)
	require.Equal(t, 86400, usage.TotalTokens)
	require.InDelta(t, 1.4904, usage.ChargedAmount, 0.000001)
	require.Zero(t, usage.ReservedAmount)
	require.Zero(t, usage.RefundedAmount)
	require.Equal(t, "CNY", usage.Currency)
	require.Equal(t, "settled", usage.BillingStatus)
}

func TestTaskVideoUsageDistinguishesReservedAndRefundedAmounts(t *testing.T) {
	queued := (&Task{
		Quota:  599400,
		Status: TaskStatusQueued,
		PrivateData: TaskPrivateData{BillingContext: &TaskBillingContext{
			QuotaPerUnit:  500000,
			BillingStatus: "reserved",
		}},
	}).VideoUsage()
	require.Zero(t, queued.ChargedAmount)
	require.InDelta(t, 1.1988, queued.ReservedAmount, 0.000001)
	require.Equal(t, "reserved", queued.BillingStatus)
	require.Equal(t, "CNY", queued.Currency)

	refundPending := (&Task{
		Quota:  599400,
		Status: TaskStatusFailure,
		PrivateData: TaskPrivateData{BillingContext: &TaskBillingContext{
			QuotaPerUnit:  500000,
			BillingStatus: "refund_pending",
		}},
	}).VideoUsage()
	require.InDelta(t, 1.1988, refundPending.ChargedAmount, 0.000001)
	require.InDelta(t, 1.1988, refundPending.PendingRefundAmount, 0.000001)
	require.Zero(t, refundPending.RefundedAmount)
	require.Equal(t, "refund_pending", refundPending.BillingStatus)

	refunded := (&Task{
		Quota:  599400,
		Status: TaskStatusFailure,
		PrivateData: TaskPrivateData{BillingContext: &TaskBillingContext{
			QuotaPerUnit:  500000,
			BillingStatus: "refunded",
			RefundedQuota: 599400,
		}},
	}).VideoUsage()
	require.Zero(t, refunded.ChargedAmount)
	require.InDelta(t, 1.1988, refunded.RefundedAmount, 0.000001)
	require.Equal(t, "refunded", refunded.BillingStatus)
}

func TestTaskOpenAIVideoIncludesPublicUsageWithoutInternalQuota(t *testing.T) {
	task := &Task{
		TaskID: "task_public",
		Quota:  745200,
		Status: TaskStatusSuccess,
		PrivateData: TaskPrivateData{BillingContext: &TaskBillingContext{
			QuotaPerUnit:    500000,
			BillingCurrency: "CNY",
			BillingStatus:   "settled",
			TotalTokens:     86400,
		}},
	}

	payload, err := json.Marshal(task.ToOpenAIVideo())
	require.NoError(t, err)
	require.Contains(t, string(payload), `"usage":{"total_tokens":86400`)
	require.Contains(t, string(payload), `"charged_amount":1.4904`)
	require.NotContains(t, string(payload), `"quota"`)
}

func TestTaskVideoUsageDoesNotGuessLegacyConversion(t *testing.T) {
	usage := (&Task{Quota: 745200, Status: TaskStatusSuccess}).VideoUsage()

	require.Zero(t, usage.ChargedAmount)
	require.Equal(t, "CNY", usage.Currency)
	require.Equal(t, "unavailable", usage.BillingStatus)
}
