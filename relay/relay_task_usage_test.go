package relay

import (
	"encoding/json"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
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

func TestV2PublicVideoPayloadRedactsPrivateRoutingAndCost(t *testing.T) {
	task := &model.Task{
		TaskID:    "task_v2_public",
		Quota:     745200,
		ChannelId: 42,
		Status:    model.TaskStatusSuccess,
		Properties: model.Properties{
			OriginModelName:   "seedance-2.0",
			UpstreamModelName: "sd2-720p",
		},
		PrivateData: model.TaskPrivateData{BillingContext: &model.TaskBillingContext{
			ContractVersion: "xtai-video-billing-v2",
			QuotaPerUnit:    500000,
			BillingStatus:   "settled",
		}},
	}

	payload, err := attachOpenAIVideoUsage(task, []byte(`{
		"id":"task_v2_public","status":"completed","url":"https://result.example/video.mp4",
		"provider_id":"paisio","upstream_task_id":"private-1","actual_cost_cny":"0.29",
		"metadata":{"channel_id":42,"margin":0.5,"safe":"kept"}
	}`))
	require.NoError(t, err)
	text := string(payload)
	require.Contains(t, text, `"url":"https://result.example/video.mp4"`)
	require.Contains(t, text, `"safe":"kept"`)
	require.NotContains(t, text, "paisio")
	require.NotContains(t, text, "upstream_task_id")
	require.NotContains(t, text, "actual_cost")
	require.NotContains(t, text, "channel_id")
	require.NotContains(t, text, "margin")

	dtoValue := TaskModel2Dto(task)
	require.Zero(t, dtoValue.ChannelId)
	require.Zero(t, dtoValue.Quota)
	properties, ok := dtoValue.Properties.(model.Properties)
	require.True(t, ok)
	require.Empty(t, properties.UpstreamModelName)
}

func TestXingTuV2QueryUsesCanonicalContract(t *testing.T) {
	previousDB := model.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&model.Task{}))
	model.DB = db
	t.Cleanup(func() { model.DB = previousDB })

	task := &model.Task{
		TaskID:     "task_contract",
		UserId:     77,
		Quota:      1087500,
		Status:     model.TaskStatusSuccess,
		Progress:   "100%",
		Properties: model.Properties{OriginModelName: "seedance-2.0", UpstreamModelName: "private-model"},
		PrivateData: model.TaskPrivateData{
			RequestID:      "req_contract_0001",
			UpstreamTaskID: "private-upstream-id",
			ResultURL:      "https://result.example/video.mp4",
			BillingContext: &model.TaskBillingContext{
				ContractVersion: service.XingTuVideoContractV2,
				QuotaPerUnit:    500000,
				BillingStatus:   "settled",
				ReservedQuota:   2980800,
				RefundedQuota:   1893300,
			},
		},
	}
	require.NoError(t, task.Insert())

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	request := httptest.NewRequest("GET", "/v1/videos/task_contract", nil)
	request.Header.Set(service.XingTuVideoContractHeader, service.XingTuVideoContractV2)
	context.Request = request
	context.Params = gin.Params{{Key: "task_id", Value: "task_contract"}}
	context.Set("id", 77)
	context.Set(relaycommon.XingTuVideoContractContextKey, true)

	payload, taskErr := videoFetchByIDRespBodyBuilder(context)
	require.Nil(t, taskErr)
	text := string(payload)
	require.Contains(t, text, `"request_id":"req_contract_0001"`)
	require.Contains(t, text, `"status":"succeeded"`)
	require.Contains(t, text, `"charged_amount":"2.175000"`)
	require.Contains(t, text, `"result_delivery":"ready"`)
	require.Contains(t, text, `/v1/videos/task_contract/content`)
	require.NotContains(t, text, "result.example")
	require.NotContains(t, text, "private-upstream-id")
	require.NotContains(t, text, "private-model")
}
