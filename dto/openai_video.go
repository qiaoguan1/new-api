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
	ResultDelivery     string            `json:"result_delivery,omitempty"`
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
	SupplementAmount    float64 `json:"supplement_amount,omitempty"`
	Currency            string  `json:"currency"`
	BillingStatus       string  `json:"billing_status"`
}

// XingTuVideoResponse is the versioned provider-neutral contract returned to
// XingTu software. Monetary values are fixed six-decimal CNY strings.
type XingTuVideoResponse struct {
	ID             string              `json:"id"`
	RequestID      string              `json:"request_id"`
	Object         string              `json:"object"`
	Model          string              `json:"model"`
	Status         string              `json:"status"`
	Progress       int                 `json:"progress"`
	CreatedAt      int64               `json:"created_at"`
	CompletedAt    int64               `json:"completed_at,omitempty"`
	Result         *XingTuVideoResult  `json:"result"`
	ResultDelivery string              `json:"result_delivery"`
	Billing        *XingTuVideoBilling `json:"billing"`
	Usage          *XingTuVideoUsage   `json:"usage,omitempty"`
	Error          *OpenAIVideoError   `json:"error,omitempty"`
}

type XingTuVideoResult struct {
	Type string `json:"type"`
	URL  string `json:"url"`
}

type XingTuVideoBilling struct {
	ContractVersion  string  `json:"contract_version"`
	Status           string  `json:"status"`
	Currency         string  `json:"currency"`
	ReserveBasis     string  `json:"reserve_basis"`
	ReservedAmount   string  `json:"reserved_amount"`
	ChargedAmount    *string `json:"charged_amount"`
	RefundAmount     *string `json:"refund_amount"`
	SupplementAmount *string `json:"supplement_amount"`
	Markup           string  `json:"markup"`
	PricingRevision  string  `json:"pricing_revision,omitempty"`
	SettledAt        string  `json:"settled_at,omitempty"`
}

type XingTuVideoUsage struct {
	OutputTokens *int `json:"output_tokens"`
	TotalTokens  *int `json:"total_tokens"`
}

type XingTuVideoErrorEnvelope struct {
	Error XingTuVideoPublicError `json:"error"`
}

type XingTuVideoPublicError struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	RequestID string `json:"request_id,omitempty"`
	TaskID    string `json:"task_id,omitempty"`
	Retryable bool   `json:"retryable"`
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
