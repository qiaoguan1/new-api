package relay

import (
	"strconv"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/relay/channel/task/topaz"
)

func TestGetTaskAdaptorUsesTopazForChannelType58(t *testing.T) {
	platform := constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeTopaz))
	adaptor := GetTaskAdaptor(platform)
	if adaptor == nil {
		t.Fatal("Topaz task adaptor is nil")
	}
	if _, ok := adaptor.(*topaz.TaskAdaptor); !ok {
		t.Fatalf("Topaz task adaptor = %T, want *topaz.TaskAdaptor", adaptor)
	}
}
