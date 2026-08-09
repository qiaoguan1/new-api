package relay

import (
	"encoding/json"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
)

func TestAttachOpenAIVideoUsageNormalizesEveryAdaptorResponse(t *testing.T) {
	task := &model.Task{
		Quota:  745200,
		Status: model.TaskStatusSuccess,
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{
			QuotaPerUnit:    500000,
			BillingCurrency: "CNY",
			BillingStatus:   "settled",
			TotalTokens:     86400,
		}},
	}

	payload, err := attachOpenAIVideoUsage(task, []byte(`{"id":"task_public","object":"video","status":"completed","progress":100,"created_at":1,"vendor_extension":{"keep":true}}`))
	require.NoError(t, err)

	var video dto.OpenAIVideo
	require.NoError(t, json.Unmarshal(payload, &video))
	require.NotNil(t, video.Usage)
	require.Equal(t, 86400, video.Usage.TotalTokens)
	require.InDelta(t, 1.4904, video.Usage.ChargedAmount, 0.000001)
	require.Contains(t, string(payload), `"vendor_extension":{"keep":true}`)
}

func TestTaskModel2DtoAddsUsageWithoutChangingLegacyFields(t *testing.T) {
	task := &model.Task{
		TaskID: "task_public",
		Quota:  745200,
		Status: model.TaskStatusSuccess,
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{
			QuotaPerUnit:  500000,
			BillingStatus: "settled",
		}},
	}

	response := TaskModel2Dto(task)

	require.Equal(t, "task_public", response.TaskID)
	require.Equal(t, 745200, response.Quota)
	require.NotNil(t, response.Usage)
	require.InDelta(t, 1.4904, response.Usage.ChargedAmount, 0.000001)
}
