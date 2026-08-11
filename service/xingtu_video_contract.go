package service

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"regexp"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

const (
	XingTuVideoContractHeader  = "X-XingTu-Contract-Version"
	XingTuVideoContractV2      = constant.XingTuVideoContractLegacy
	XingTuVideoContractV21     = constant.XingTuVideoContractCurrent
	XingTuVideoContractCurrent = constant.XingTuVideoContractCurrent
	XingTuVideoProviderID      = "video-aixingtu-api"
)

var xingTuRequestIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._:-]{7,127}$`)

// IsXingTuVideoContractVersion accepts the current contract and the legacy
// read/settlement window used by already-created tasks.
func IsXingTuVideoContractVersion(version string) bool {
	return constant.IsXingTuVideoContract(strings.TrimSpace(version))
}

type XingTuVideoValidation struct {
	RequestID   string
	Fingerprint string
}

type XingTuContractError struct {
	Code       string
	Message    string
	StatusCode int
	Retryable  bool
}

// ValidateXingTuVideoRequest validates the public deployment contract before
// any reservation or provider side effect occurs.
func ValidateXingTuVideoRequest(request relaycommon.TaskSubmitReq, idempotencyKey string) (*XingTuVideoValidation, *XingTuContractError) {
	idempotencyKey = strings.TrimSpace(idempotencyKey)
	requestID := strings.TrimSpace(request.RequestID)
	if request.ProviderID != XingTuVideoProviderID {
		return nil, xingTuValidationError("invalid_provider_id", "provider_id must be video-aixingtu-api")
	}
	if idempotencyKey == "" || requestID == "" {
		return nil, xingTuValidationError("missing_idempotency_key", "Idempotency-Key and request_id are required")
	}
	if idempotencyKey != requestID {
		return nil, xingTuValidationError("idempotency_key_mismatch", "Idempotency-Key must equal request_id")
	}
	if !xingTuRequestIDPattern.MatchString(requestID) {
		return nil, xingTuValidationError("invalid_request_id", "request_id must be 8-128 lowercase ASCII characters")
	}
	if strings.TrimSpace(request.Model) == "" {
		return nil, xingTuValidationError("missing_model", "model is required")
	}
	if strings.TrimSpace(request.Resolution) == "" {
		return nil, xingTuValidationError("missing_resolution", "resolution is required")
	}
	if request.Duration <= 0 {
		return nil, xingTuValidationError("invalid_duration", "duration must be greater than zero")
	}
	if strings.TrimSpace(request.AspectRatio) == "" {
		return nil, xingTuValidationError("missing_aspect_ratio", "aspect_ratio is required")
	}
	if request.GenerateAudio == nil {
		return nil, xingTuValidationError("missing_generate_audio", "generate_audio must be explicitly true or false")
	}
	if strings.TrimSpace(request.Prompt) == "" && len(request.Images) == 0 && strings.TrimSpace(request.Image) == "" {
		return nil, xingTuValidationError("missing_input", "prompt or image input is required")
	}
	fingerprint, err := BuildXingTuVideoFingerprint(request)
	if err != nil {
		return nil, &XingTuContractError{
			Code:       "request_fingerprint_failed",
			Message:    "unable to normalize request",
			StatusCode: http.StatusInternalServerError,
			Retryable:  true,
		}
	}
	return &XingTuVideoValidation{RequestID: requestID, Fingerprint: fingerprint}, nil
}

func xingTuValidationError(code, message string) *XingTuContractError {
	return &XingTuContractError{Code: code, Message: message, StatusCode: http.StatusBadRequest}
}

// BuildXingTuVideoFingerprint produces a canonical semantic request identity.
// request_id and provider_id are excluded because they define the namespace,
// while all generation-affecting fields remain part of the fingerprint.
func BuildXingTuVideoFingerprint(request relaycommon.TaskSubmitReq) (string, error) {
	payload := struct {
		Prompt         string                 `json:"prompt"`
		Model          string                 `json:"model"`
		Mode           string                 `json:"mode,omitempty"`
		Image          string                 `json:"image,omitempty"`
		Images         []string               `json:"images,omitempty"`
		Size           string                 `json:"size,omitempty"`
		Resolution     string                 `json:"resolution"`
		Duration       int                    `json:"duration"`
		Seconds        string                 `json:"seconds,omitempty"`
		AspectRatio    string                 `json:"aspect_ratio"`
		GenerateAudio  *bool                  `json:"generate_audio"`
		InputReference string                 `json:"input_reference,omitempty"`
		Metadata       map[string]interface{} `json:"metadata,omitempty"`
	}{
		Prompt:         request.Prompt,
		Model:          request.Model,
		Mode:           request.Mode,
		Image:          request.Image,
		Images:         request.Images,
		Size:           request.Size,
		Resolution:     request.Resolution,
		Duration:       request.Duration,
		Seconds:        request.Seconds,
		AspectRatio:    request.AspectRatio,
		GenerateAudio:  request.GenerateAudio,
		InputReference: request.InputReference,
		Metadata:       request.Metadata,
	}
	encoded, err := common.Marshal(payload)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}
