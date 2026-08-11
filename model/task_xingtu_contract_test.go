package model

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestXingTuVideoResponseUsesOneExactSixDecimalContract(t *testing.T) {
	task := &Task{
		TaskID:     "task_public",
		Quota:      1087500,
		Status:     TaskStatusSuccess,
		Progress:   "100%",
		CreatedAt:  100,
		UpdatedAt:  200,
		Properties: Properties{OriginModelName: "seedance-2.0", UpstreamModelName: "private-model"},
		PrivateData: TaskPrivateData{
			RequestID:      "req_20260811_000001",
			UpstreamTaskID: "private-provider-task",
			ResultURL:      "https://result.example/video.mp4",
			BillingContext: &TaskBillingContext{
				ContractVersion:         "xtai-video-billing-v2",
				QuotaPerUnit:            500000,
				BillingCurrency:         "CNY",
				BillingStatus:           "settled",
				ReservedQuota:           2980800,
				RefundedQuota:           1893300,
				SupplementedQuota:       0,
				OfficialPricingRevision: "official-fallback-2026-08-09.1",
			},
		},
	}

	response := task.ToXingTuVideo()
	payload, err := json.Marshal(response)
	require.NoError(t, err)
	text := string(payload)

	require.Equal(t, "req_20260811_000001", response.RequestID)
	require.Equal(t, "succeeded", response.Status)
	require.Equal(t, "ready", response.ResultDelivery)
	require.NotNil(t, response.Result)
	require.Equal(t, "http://localhost:3000/v1/videos/task_public/content", response.Result.URL)
	require.Equal(t, "5.961600", response.Billing.ReservedAmount)
	require.Equal(t, "2.175000", *response.Billing.ChargedAmount)
	require.Equal(t, "3.786600", *response.Billing.RefundAmount)
	require.Equal(t, "0.000000", *response.Billing.SupplementAmount)
	require.Contains(t, text, `"charged_amount":"2.175000"`)
	require.NotContains(t, text, "private-provider-task")
	require.NotContains(t, text, "private-model")
	require.NotContains(t, text, "result.example")
	require.NotContains(t, text, "actual_cost")
}

func TestXingTuVideoResponseWithholdsResultWhileSettlementPending(t *testing.T) {
	task := &Task{
		TaskID:     "task_pending",
		Quota:      2980800,
		Status:     TaskStatusSuccess,
		Progress:   "100%",
		Properties: Properties{OriginModelName: "seedance-2.0"},
		PrivateData: TaskPrivateData{
			RequestID: "req_pending",
			ResultURL: "https://private.example/video.mp4",
			BillingContext: &TaskBillingContext{
				ContractVersion: "xtai-video-billing-v2",
				QuotaPerUnit:    500000,
				BillingStatus:   "settlement_pending",
				ReservedQuota:   2980800,
			},
		},
	}

	response := task.ToXingTuVideo()
	require.Equal(t, "succeeded", response.Status)
	require.Equal(t, "pending_settlement", response.ResultDelivery)
	require.Nil(t, response.Result)
	require.Equal(t, "settlement_pending", response.Billing.Status)
	require.Equal(t, "5.961600", response.Billing.ReservedAmount)
	require.Nil(t, response.Billing.ChargedAmount)
	require.Nil(t, response.Billing.RefundAmount)
	require.Nil(t, response.Billing.SupplementAmount)
}

func TestXingTuVideoFailureDoesNotExposeRawProviderReason(t *testing.T) {
	task := &Task{
		TaskID:     "task_failed",
		Status:     TaskStatusFailure,
		FailReason: "paisio private task abc failed with upstream secret detail",
		PrivateData: TaskPrivateData{
			RequestID: "req_failed_0001",
			BillingContext: &TaskBillingContext{
				ContractVersion: "xtai-video-billing-v2",
				QuotaPerUnit:    500000,
				BillingStatus:   "refunded",
				ReservedQuota:   500000,
				RefundedQuota:   500000,
			},
		},
	}

	payload, err := json.Marshal(task.ToXingTuVideo())
	require.NoError(t, err)
	require.Contains(t, string(payload), `"message":"video generation failed"`)
	require.NotContains(t, string(payload), "paisio")
	require.NotContains(t, string(payload), "secret detail")
}
