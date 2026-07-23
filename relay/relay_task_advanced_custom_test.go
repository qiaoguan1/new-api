package relay

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
)

func TestConvertStoredAdvancedCustomVideo(t *testing.T) {
	task := &model.Task{
		TaskID:    "task_advanced_custom",
		Platform:  constant.TaskPlatform("58"),
		Status:    model.TaskStatusSuccess,
		Progress:  "100%",
		CreatedAt: 100,
		UpdatedAt: 200,
		Properties: model.Properties{
			OriginModelName: "prob-4",
		},
		PrivateData: model.TaskPrivateData{
			ResultURL: "https://example.test/result.mp4",
		},
	}

	body, handled, err := convertStoredAdvancedCustomVideo(task)
	if err != nil {
		t.Fatalf("convert stored advanced custom video: %v", err)
	}
	if !handled {
		t.Fatal("advanced custom task was not handled")
	}

	var video dto.OpenAIVideo
	if err := common.Unmarshal(body, &video); err != nil {
		t.Fatalf("decode OpenAI video: %v", err)
	}
	if video.ID != task.TaskID || video.Status != dto.VideoStatusCompleted || video.Progress != 100 {
		t.Fatalf("unexpected task projection: %+v", video)
	}
	if video.Model != "prob-4" || video.Metadata["url"] != "https://example.test/result.mp4" {
		t.Fatalf("unexpected model or result URL: %+v", video)
	}

	unknown := &model.Task{Platform: constant.TaskPlatform("999")}
	unknownBody, unknownHandled, unknownErr := convertStoredAdvancedCustomVideo(unknown)
	if unknownErr != nil || unknownHandled || unknownBody != nil {
		t.Fatalf("unknown platform must remain unhandled: body=%q handled=%v err=%v", unknownBody, unknownHandled, unknownErr)
	}

	nilBody, nilHandled, nilErr := convertStoredAdvancedCustomVideo(nil)
	if nilErr != nil || nilHandled || nilBody != nil {
		t.Fatalf("nil task must remain unhandled: body=%q handled=%v err=%v", nilBody, nilHandled, nilErr)
	}
}
