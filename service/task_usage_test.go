package service

import (
	"testing"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/require"
)

func TestCaptureTaskUsagePersistsProviderReportedTokens(t *testing.T) {
	task := &model.Task{PrivateData: model.TaskPrivateData{
		BillingContext: &model.TaskBillingContext{},
	}}

	captureTaskUsage(task, &relaycommon.TaskInfo{
		CompletionTokens: 82000,
		TotalTokens:      86400,
	})

	require.Equal(t, 82000, task.PrivateData.BillingContext.CompletionTokens)
	require.Equal(t, 86400, task.PrivateData.BillingContext.TotalTokens)
	require.Equal(t, "", task.PrivateData.BillingContext.BillingStatus)
}

func TestCaptureTaskUsageRecordsTerminalBillingState(t *testing.T) {
	succeeded := &model.Task{
		Status:      model.TaskStatusSuccess,
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{}},
	}
	captureTaskUsage(succeeded, &relaycommon.TaskInfo{})
	require.Equal(t, "settled", succeeded.PrivateData.BillingContext.BillingStatus)

	failed := &model.Task{
		Status:      model.TaskStatusFailure,
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{}},
	}
	captureTaskUsage(failed, &relaycommon.TaskInfo{})
	require.Equal(t, "refund_pending", failed.PrivateData.BillingContext.BillingStatus)
}

func TestCaptureTaskUsageDoesNotErasePreviousTokens(t *testing.T) {
	task := &model.Task{PrivateData: model.TaskPrivateData{
		BillingContext: &model.TaskBillingContext{
			CompletionTokens: 82000,
			TotalTokens:      86400,
		},
	}}

	captureTaskUsage(task, &relaycommon.TaskInfo{})

	require.Equal(t, 82000, task.PrivateData.BillingContext.CompletionTokens)
	require.Equal(t, 86400, task.PrivateData.BillingContext.TotalTokens)
}
