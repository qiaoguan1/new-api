package dto

import (
	"strconv"
	"strings"
)

const (
	VideoStatusUnknown    = "unknown"
	VideoStatusQueued     = "queued"
	VideoStatusInProgress = "in_progress"
	VideoStatusCompleted  = "completed"
	VideoStatusFailed     = "failed"
)

type OpenAIVideo struct {
	ID                 string            `json:"id"`
	TaskID             string            `json:"task_id,omitempty"` //兼容旧接口 待废弃
	Object             string            `json:"object"`
	Model              string            `json:"model"`
	Status             string            `json:"status"` // Should use VideoStatus constants: VideoStatusQueued, VideoStatusInProgress, VideoStatusCompleted, VideoStatusFailed
	Progress           int               `json:"progress"`
	CreatedAt          int64             `json:"created_at"`
	CompletedAt        int64             `json:"completed_at,omitempty"`
	ExpiresAt          int64             `json:"expires_at,omitempty"`
	Seconds            string            `json:"seconds,omitempty"`
	Size               string            `json:"size,omitempty"`
	RemixedFromVideoID string            `json:"remixed_from_video_id,omitempty"`
	Error              *OpenAIVideoError `json:"error,omitempty"`
	Metadata           map[string]any    `json:"metadata,omitempty"`
	Usage              *VideoUsage       `json:"usage,omitempty"`
}

// VideoUsage is the provider-neutral public billing result for a video task.
// Token fields are omitted when the upstream does not report them.
type VideoUsage struct {
	OutputTokens        int     `json:"output_tokens,omitempty"`
	TotalTokens         int     `json:"total_tokens,omitempty"`
	ChargedAmount       float64 `json:"charged_amount"`
	ReservedAmount      float64 `json:"reserved_amount,omitempty"`
	PendingRefundAmount float64 `json:"pending_refund_amount,omitempty"`
	RefundedAmount      float64 `json:"refunded_amount,omitempty"`
	Currency            string  `json:"currency"`
	BillingStatus       string  `json:"billing_status"`
}

func (m *OpenAIVideo) SetProgressStr(progress string) {
	progress = strings.TrimSuffix(progress, "%")
	m.Progress, _ = strconv.Atoi(progress)
}
func (m *OpenAIVideo) SetMetadata(k string, v any) {
	if m.Metadata == nil {
		m.Metadata = make(map[string]any)
	}
	m.Metadata[k] = v
}
func NewOpenAIVideo() *OpenAIVideo {
	return &OpenAIVideo{
		Object: "video",
		Status: VideoStatusQueued,
	}
}

type OpenAIVideoError struct {
	Message string `json:"message"`
	Code    string `json:"code"`
}
