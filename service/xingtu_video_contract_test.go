package service

import (
	"testing"

	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/require"
)

func validXingTuRequest() relaycommon.TaskSubmitReq {
	audio := true
	return relaycommon.TaskSubmitReq{
		ProviderID:    XingTuVideoProviderID,
		RequestID:     "req_20260811_000001",
		Model:         "seedance-2.0",
		Resolution:    "720p",
		Duration:      4,
		AspectRatio:   "16:9",
		GenerateAudio: &audio,
		Prompt:        "hello",
		Metadata: map[string]interface{}{
			"aspect_ratio":   "16:9",
			"generate_audio": true,
		},
	}
}

func TestValidateXingTuVideoRequestRequiresMatchingStableIdempotency(t *testing.T) {
	request := validXingTuRequest()

	validated, contractErr := ValidateXingTuVideoRequest(request, request.RequestID)
	require.Nil(t, contractErr)
	require.Equal(t, request.RequestID, validated.RequestID)
	require.Len(t, validated.Fingerprint, 64)

	_, contractErr = ValidateXingTuVideoRequest(request, "req_different_000001")
	require.NotNil(t, contractErr)
	require.Equal(t, "idempotency_key_mismatch", contractErr.Code)

	request = validXingTuRequest()
	request.RequestID = "Req_Uppercase_0001"
	_, contractErr = ValidateXingTuVideoRequest(request, request.RequestID)
	require.NotNil(t, contractErr)
	require.Equal(t, "invalid_request_id", contractErr.Code)
}

func TestValidateXingTuVideoRequestRequiresExplicitAudioAndProvider(t *testing.T) {
	request := validXingTuRequest()
	request.GenerateAudio = nil
	_, contractErr := ValidateXingTuVideoRequest(request, request.RequestID)
	require.NotNil(t, contractErr)
	require.Equal(t, "missing_generate_audio", contractErr.Code)

	request = validXingTuRequest()
	request.ProviderID = "paisio"
	_, contractErr = ValidateXingTuVideoRequest(request, request.RequestID)
	require.NotNil(t, contractErr)
	require.Equal(t, "invalid_provider_id", contractErr.Code)
}

func TestXingTuVideoFingerprintIsCanonicalAndPayloadSensitive(t *testing.T) {
	first := validXingTuRequest()
	second := validXingTuRequest()
	second.Metadata = map[string]interface{}{
		"generate_audio": true,
		"aspect_ratio":   "16:9",
	}

	a, errA := BuildXingTuVideoFingerprint(first)
	b, errB := BuildXingTuVideoFingerprint(second)
	require.NoError(t, errA)
	require.NoError(t, errB)
	require.Equal(t, a, b)

	second.Prompt = "changed"
	c, errC := BuildXingTuVideoFingerprint(second)
	require.NoError(t, errC)
	require.NotEqual(t, a, c)
}
