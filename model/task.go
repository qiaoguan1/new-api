package model

import (
	"bytes"
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	commonRelay "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/setting/system_setting"
)

type TaskStatus string

func (t TaskStatus) ToVideoStatus() string {
	var status string
	switch t {
	case TaskStatusQueued, TaskStatusSubmitted:
		status = dto.VideoStatusQueued
	case TaskStatusInProgress:
		status = dto.VideoStatusInProgress
	case TaskStatusSuccess:
		status = dto.VideoStatusCompleted
	case TaskStatusFailure:
		status = dto.VideoStatusFailed
	default:
		status = dto.VideoStatusUnknown // Default fallback
	}
	return status
}

const (
	TaskStatusNotStart   TaskStatus = "NOT_START"
	TaskStatusSubmitted             = "SUBMITTED"
	TaskStatusQueued                = "QUEUED"
	TaskStatusInProgress            = "IN_PROGRESS"
	TaskStatusFailure               = "FAILURE"
	TaskStatusSuccess               = "SUCCESS"
	TaskStatusUnknown               = "UNKNOWN"
)

type Task struct {
	ID         int64                 `json:"id" gorm:"primary_key;AUTO_INCREMENT"`
	CreatedAt  int64                 `json:"created_at" gorm:"index"`
	UpdatedAt  int64                 `json:"updated_at"`
	TaskID     string                `json:"task_id" gorm:"type:varchar(191);index"` // 第三方id，不一定有/ song id\ Task id
	Platform   constant.TaskPlatform `json:"platform" gorm:"type:varchar(30);index"` // 平台
	UserId     int                   `json:"user_id" gorm:"index"`
	Group      string                `json:"group" gorm:"type:varchar(50)"` // 修正计费用
	ChannelId  int                   `json:"channel_id" gorm:"index"`
	Quota      int                   `json:"quota"`
	Action     string                `json:"action" gorm:"type:varchar(40);index"` // 任务类型, song, lyrics, description-mode
	Status     TaskStatus            `json:"status" gorm:"type:varchar(20);index"` // 任务状态
	FailReason string                `json:"fail_reason"`
	SubmitTime int64                 `json:"submit_time" gorm:"index"`
	StartTime  int64                 `json:"start_time" gorm:"index"`
	FinishTime int64                 `json:"finish_time" gorm:"index"`
	Progress   string                `json:"progress" gorm:"type:varchar(20);index"`
	Properties Properties            `json:"properties" gorm:"type:json"`
	Username   string                `json:"username,omitempty" gorm:"-"`
	// 禁止返回给用户，内部可能包含key等隐私信息
	PrivateData TaskPrivateData `json:"-" gorm:"column:private_data;type:json"`
	Data        json.RawMessage `json:"data" gorm:"type:json"`
}

func (t *Task) SetData(data any) {
	b, _ := common.Marshal(data)
	t.Data = json.RawMessage(b)
}

func (t *Task) GetData(v any) error {
	return common.Unmarshal(t.Data, &v)
}

type Properties struct {
	Input             string `json:"input"`
	UpstreamModelName string `json:"upstream_model_name,omitempty"`
	OriginModelName   string `json:"origin_model_name,omitempty"`
}

func (m *Properties) Scan(val interface{}) error {
	bytesValue, _ := val.([]byte)
	if len(bytesValue) == 0 {
		*m = Properties{}
		return nil
	}
	return common.Unmarshal(bytesValue, m)
}

func (m Properties) Value() (driver.Value, error) {
	if m == (Properties{}) {
		return nil, nil
	}
	return common.Marshal(m)
}

type TaskPrivateData struct {
	Key                string `json:"key,omitempty"`
	RequestID          string `json:"request_id,omitempty"`
	RequestFingerprint string `json:"request_fingerprint,omitempty"`
	UpstreamTaskID     string `json:"upstream_task_id,omitempty"` // 上游真实 task ID
	ResultURL          string `json:"result_url,omitempty"`       // 任务成功后的结果 URL（视频地址等）
	// 计费上下文：用于异步退款/差额结算（轮询阶段读取）
	BillingSource  string              `json:"billing_source,omitempty"`  // "wallet" 或 "subscription"
	SubscriptionId int                 `json:"subscription_id,omitempty"` // 订阅 ID，用于订阅退款
	TokenId        int                 `json:"token_id,omitempty"`        // 令牌 ID，用于令牌额度退款
	BillingContext *TaskBillingContext `json:"billing_context,omitempty"` // 计费参数快照（用于轮询阶段重新计算）
}

// TaskBillingContext 记录任务提交时的计费参数，以便轮询阶段可以重新计算额度。
type TaskBillingContext struct {
	ContractVersion         string             `json:"contract_version,omitempty"`
	QuotaPerUnit            float64            `json:"quota_per_unit,omitempty"`
	BillingCurrency         string             `json:"billing_currency,omitempty"`
	BillingStatus           string             `json:"billing_status,omitempty"`
	ReservedQuota           int                `json:"reserved_quota,omitempty"`
	ChargedQuota            int                `json:"charged_quota,omitempty"`
	SupplementedQuota       int                `json:"supplemented_quota,omitempty"`
	SettlementID            string             `json:"settlement_id,omitempty"`
	SettlementRevision      int                `json:"settlement_revision,omitempty"`
	SettlementFingerprint   string             `json:"settlement_fingerprint,omitempty"`
	OfficialPricingRevision string             `json:"official_pricing_revision,omitempty"`
	CompletionTokens        int                `json:"completion_tokens,omitempty"`
	TotalTokens             int                `json:"total_tokens,omitempty"`
	RefundedQuota           int                `json:"refunded_quota,omitempty"`
	ModelPrice              float64            `json:"model_price,omitempty"`       // 模型单价
	GroupRatio              float64            `json:"group_ratio,omitempty"`       // 分组倍率
	ModelRatio              float64            `json:"model_ratio,omitempty"`       // 模型倍率
	OtherRatios             map[string]float64 `json:"other_ratios,omitempty"`      // 附加倍率（时长、分辨率等）
	OriginModelName         string             `json:"origin_model_name,omitempty"` // 模型名称，必须为OriginModelName
	PerCallBilling          bool               `json:"per_call_billing,omitempty"`  // 按次计费：跳过轮询阶段的差额结算
}

// GetUpstreamTaskID 获取上游真实 task ID（用于与 provider 通信）
// 旧数据没有 UpstreamTaskID 时，TaskID 本身就是上游 ID
func (t *Task) GetUpstreamTaskID() string {
	if t.PrivateData.UpstreamTaskID != "" {
		return t.PrivateData.UpstreamTaskID
	}
	return t.TaskID
}

// GetResultURL 获取任务结果 URL（视频地址等）
// 新数据存在 PrivateData.ResultURL 中；旧数据回退到 FailReason（历史兼容）
func (t *Task) GetResultURL() string {
	if t.PrivateData.ResultURL != "" {
		return t.PrivateData.ResultURL
	}
	return t.FailReason
}

// GenerateTaskID 生成对外暴露的 task_xxxx 格式 ID
func GenerateTaskID() string {
	key, _ := common.GenerateRandomCharsKey(32)
	return "task_" + key
}

func (p *TaskPrivateData) Scan(val interface{}) error {
	bytesValue, _ := val.([]byte)
	if len(bytesValue) == 0 {
		return nil
	}
	return common.Unmarshal(bytesValue, p)
}

func (p TaskPrivateData) Value() (driver.Value, error) {
	if (p == TaskPrivateData{}) {
		return nil, nil
	}
	return common.Marshal(p)
}

// SyncTaskQueryParams 用于包含所有搜索条件的结构体，可以根据需求添加更多字段
type SyncTaskQueryParams struct {
	Platform       constant.TaskPlatform
	ChannelID      string
	TaskID         string
	UserID         string
	Action         string
	Status         string
	StartTimestamp int64
	EndTimestamp   int64
	UserIDs        []int
}

func InitTask(platform constant.TaskPlatform, relayInfo *commonRelay.RelayInfo) *Task {
	properties := Properties{}
	privateData := TaskPrivateData{}
	if relayInfo != nil && relayInfo.ChannelMeta != nil {
		if relayInfo.ChannelMeta.ChannelType == constant.ChannelTypeGemini ||
			relayInfo.ChannelMeta.ChannelType == constant.ChannelTypeVertexAi {
			privateData.Key = relayInfo.ChannelMeta.ApiKey
		}
		if relayInfo.UpstreamModelName != "" {
			properties.UpstreamModelName = relayInfo.UpstreamModelName
		}
		if relayInfo.OriginModelName != "" {
			properties.OriginModelName = relayInfo.OriginModelName
		}
	}

	// 使用预生成的公开 ID（如果有），否则新生成
	taskID := ""
	if relayInfo.TaskRelayInfo != nil && relayInfo.TaskRelayInfo.PublicTaskID != "" {
		taskID = relayInfo.TaskRelayInfo.PublicTaskID
	} else {
		taskID = GenerateTaskID()
	}

	t := &Task{
		TaskID:      taskID,
		UserId:      relayInfo.UserId,
		Group:       relayInfo.UsingGroup,
		SubmitTime:  time.Now().Unix(),
		Status:      TaskStatusNotStart,
		Progress:    "0%",
		ChannelId:   relayInfo.ChannelId,
		Platform:    platform,
		Properties:  properties,
		PrivateData: privateData,
	}
	return t
}

func TaskGetAllUserTask(userId int, startIdx int, num int, queryParams SyncTaskQueryParams) []*Task {
	var tasks []*Task
	var err error

	// 初始化查询构建器
	query := DB.Where("user_id = ?", userId)

	if queryParams.TaskID != "" {
		query = query.Where("task_id = ?", queryParams.TaskID)
	}
	if queryParams.Action != "" {
		query = query.Where("action = ?", queryParams.Action)
	}
	if queryParams.Status != "" {
		query = query.Where("status = ?", queryParams.Status)
	}
	if queryParams.Platform != "" {
		query = query.Where("platform = ?", queryParams.Platform)
	}
	if queryParams.StartTimestamp != 0 {
		// 假设您已将前端传来的时间戳转换为数据库所需的时间格式，并处理了时间戳的验证和解析
		query = query.Where("submit_time >= ?", queryParams.StartTimestamp)
	}
	if queryParams.EndTimestamp != 0 {
		query = query.Where("submit_time <= ?", queryParams.EndTimestamp)
	}

	// 获取数据
	err = query.Omit("channel_id").Order("id desc").Limit(num).Offset(startIdx).Find(&tasks).Error
	if err != nil {
		return nil
	}

	return tasks
}

func TaskGetAllTasks(startIdx int, num int, queryParams SyncTaskQueryParams) []*Task {
	var tasks []*Task
	var err error

	// 初始化查询构建器
	query := DB

	// 添加过滤条件
	if queryParams.ChannelID != "" {
		query = query.Where("channel_id = ?", queryParams.ChannelID)
	}
	if queryParams.Platform != "" {
		query = query.Where("platform = ?", queryParams.Platform)
	}
	if queryParams.UserID != "" {
		query = query.Where("user_id = ?", queryParams.UserID)
	}
	if len(queryParams.UserIDs) != 0 {
		query = query.Where("user_id in (?)", queryParams.UserIDs)
	}
	if queryParams.TaskID != "" {
		query = query.Where("task_id = ?", queryParams.TaskID)
	}
	if queryParams.Action != "" {
		query = query.Where("action = ?", queryParams.Action)
	}
	if queryParams.Status != "" {
		query = query.Where("status = ?", queryParams.Status)
	}
	if queryParams.StartTimestamp != 0 {
		query = query.Where("submit_time >= ?", queryParams.StartTimestamp)
	}
	if queryParams.EndTimestamp != 0 {
		query = query.Where("submit_time <= ?", queryParams.EndTimestamp)
	}

	// 获取数据
	err = query.Order("id desc").Limit(num).Offset(startIdx).Find(&tasks).Error
	if err != nil {
		return nil
	}

	return tasks
}

func GetTimedOutUnfinishedTasks(cutoffUnix int64, limit int) []*Task {
	var tasks []*Task
	err := DB.Where("progress != ?", "100%").
		Where("status NOT IN ?", []string{TaskStatusFailure, TaskStatusSuccess}).
		Where("submit_time < ?", cutoffUnix).
		Order("submit_time").
		Limit(limit).
		Find(&tasks).Error
	if err != nil {
		return nil
	}
	return tasks
}

func GetAllUnFinishSyncTasks(limit int) []*Task {
	var tasks []*Task
	var err error
	// get all tasks progress is not 100%
	err = DB.Where("progress != ?", "100%").Where("status != ?", TaskStatusFailure).Where("status != ?", TaskStatusSuccess).Limit(limit).Order("id").Find(&tasks).Error
	if err != nil {
		return nil
	}
	return tasks
}

func GetByOnlyTaskId(taskId string) (*Task, bool, error) {
	if taskId == "" {
		return nil, false, nil
	}
	var task *Task
	var err error
	err = DB.Where("task_id = ?", taskId).First(&task).Error
	exist, err := RecordExist(err)
	if err != nil {
		return nil, false, err
	}
	return task, exist, err
}

func GetByTaskId(userId int, taskId string) (*Task, bool, error) {
	if taskId == "" {
		return nil, false, nil
	}
	var task *Task
	var err error
	err = DB.Where("user_id = ? and task_id = ?", userId, taskId).
		First(&task).Error
	exist, err := RecordExist(err)
	if err != nil {
		return nil, false, err
	}
	return task, exist, err
}

func GetByTaskIds(userId int, taskIds []any) ([]*Task, error) {
	if len(taskIds) == 0 {
		return nil, nil
	}
	var task []*Task
	var err error
	err = DB.Where("user_id = ? and task_id in (?)", userId, taskIds).
		Find(&task).Error
	if err != nil {
		return nil, err
	}
	return task, nil
}

func (Task *Task) Insert() error {
	var err error
	err = DB.Create(Task).Error
	return err
}

type taskSnapshot struct {
	Status     TaskStatus
	Progress   string
	StartTime  int64
	FinishTime int64
	FailReason string
	ResultURL  string
	Data       json.RawMessage
}

func (s taskSnapshot) Equal(other taskSnapshot) bool {
	return s.Status == other.Status &&
		s.Progress == other.Progress &&
		s.StartTime == other.StartTime &&
		s.FinishTime == other.FinishTime &&
		s.FailReason == other.FailReason &&
		s.ResultURL == other.ResultURL &&
		bytes.Equal(s.Data, other.Data)
}

func (t *Task) Snapshot() taskSnapshot {
	return taskSnapshot{
		Status:     t.Status,
		Progress:   t.Progress,
		StartTime:  t.StartTime,
		FinishTime: t.FinishTime,
		FailReason: t.FailReason,
		ResultURL:  t.PrivateData.ResultURL,
		Data:       t.Data,
	}
}

func (Task *Task) Update() error {
	var err error
	err = DB.Save(Task).Error
	return err
}

// UpdatePrivateData persists only the private task snapshot so billing state
// can advance after the terminal task status CAS has completed.
func (t *Task) UpdatePrivateData() error {
	if t.ID <= 0 {
		return nil
	}
	return DB.Model(&Task{}).Where("id = ?", t.ID).Update("private_data", t.PrivateData).Error
}

// UpdateWithStatus performs a conditional UPDATE guarded by fromStatus (CAS).
// Returns (true, nil) if this caller won the update, (false, nil) if
// another process already moved the task out of fromStatus.
//
// Uses Model().Select("*").Updates() instead of Save() because GORM's Save
// falls back to INSERT ON CONFLICT when the WHERE-guarded UPDATE matches
// zero rows, which silently bypasses the CAS guard.
func (t *Task) UpdateWithStatus(fromStatus TaskStatus) (bool, error) {
	result := DB.Model(t).Where("status = ?", fromStatus).Select("*").Updates(t)
	if result.Error != nil {
		return false, result.Error
	}
	return result.RowsAffected > 0, nil
}

// TaskBulkUpdate performs an unconditional bulk UPDATE by upstream task_id strings.
// Same caveats as TaskBulkUpdateByID — no CAS guard.
func TaskBulkUpdate(taskIds []string, params map[string]any) error {
	if len(taskIds) == 0 {
		return nil
	}
	return DB.Model(&Task{}).
		Where("task_id in (?)", taskIds).
		Updates(params).Error
}

// TaskBulkUpdateByID performs an unconditional bulk UPDATE by primary key IDs.
// WARNING: This function has NO CAS (Compare-And-Swap) guard — it will overwrite
// any concurrent status changes. DO NOT use in billing/quota lifecycle flows
// (e.g., timeout, success, failure transitions that trigger refunds or settlements).
// For status transitions that involve billing, use Task.UpdateWithStatus() instead.
func TaskBulkUpdateByID(ids []int64, params map[string]any) error {
	if len(ids) == 0 {
		return nil
	}
	return DB.Model(&Task{}).
		Where("id in (?)", ids).
		Updates(params).Error
}

type TaskQuotaUsage struct {
	Mode  string  `json:"mode"`
	Count float64 `json:"count"`
}

// TaskCountAllTasks returns total tasks that match the given query params (admin usage)
func TaskCountAllTasks(queryParams SyncTaskQueryParams) int64 {
	var total int64
	query := DB.Model(&Task{})
	if queryParams.ChannelID != "" {
		query = query.Where("channel_id = ?", queryParams.ChannelID)
	}
	if queryParams.Platform != "" {
		query = query.Where("platform = ?", queryParams.Platform)
	}
	if queryParams.UserID != "" {
		query = query.Where("user_id = ?", queryParams.UserID)
	}
	if len(queryParams.UserIDs) != 0 {
		query = query.Where("user_id in (?)", queryParams.UserIDs)
	}
	if queryParams.TaskID != "" {
		query = query.Where("task_id = ?", queryParams.TaskID)
	}
	if queryParams.Action != "" {
		query = query.Where("action = ?", queryParams.Action)
	}
	if queryParams.Status != "" {
		query = query.Where("status = ?", queryParams.Status)
	}
	if queryParams.StartTimestamp != 0 {
		query = query.Where("submit_time >= ?", queryParams.StartTimestamp)
	}
	if queryParams.EndTimestamp != 0 {
		query = query.Where("submit_time <= ?", queryParams.EndTimestamp)
	}
	_ = query.Count(&total).Error
	return total
}

// TaskCountAllUserTask returns total tasks for given user
func TaskCountAllUserTask(userId int, queryParams SyncTaskQueryParams) int64 {
	var total int64
	query := DB.Model(&Task{}).Where("user_id = ?", userId)
	if queryParams.TaskID != "" {
		query = query.Where("task_id = ?", queryParams.TaskID)
	}
	if queryParams.Action != "" {
		query = query.Where("action = ?", queryParams.Action)
	}
	if queryParams.Status != "" {
		query = query.Where("status = ?", queryParams.Status)
	}
	if queryParams.Platform != "" {
		query = query.Where("platform = ?", queryParams.Platform)
	}
	if queryParams.StartTimestamp != 0 {
		query = query.Where("submit_time >= ?", queryParams.StartTimestamp)
	}
	if queryParams.EndTimestamp != 0 {
		query = query.Where("submit_time <= ?", queryParams.EndTimestamp)
	}
	_ = query.Count(&total).Error
	return total
}
func (t *Task) ToOpenAIVideo() *dto.OpenAIVideo {
	openAIVideo := dto.NewOpenAIVideo()
	openAIVideo.ID = t.TaskID
	openAIVideo.Status = t.Status.ToVideoStatus()
	openAIVideo.Model = t.Properties.OriginModelName
	openAIVideo.SetProgressStr(t.Progress)
	openAIVideo.CreatedAt = t.CreatedAt
	openAIVideo.CompletedAt = t.UpdatedAt
	openAIVideo.ResultDelivery = t.ResultDeliveryStatus()
	if openAIVideo.ResultDelivery != "pending_settlement" {
		openAIVideo.SetMetadata("url", t.GetResultURL())
	}
	openAIVideo.Usage = t.VideoUsage()
	return openAIVideo
}

func (t *Task) ResultDeliveryStatus() string {
	billing := t.PrivateData.BillingContext
	if billing == nil || billing.ContractVersion != "xtai-video-billing-v2" {
		return "ready"
	}
	if t.Status == TaskStatusSuccess && billing.BillingStatus != "settled" && billing.BillingStatus != "settled_with_debt" {
		return "pending_settlement"
	}
	if t.Status == TaskStatusSuccess {
		return "ready"
	}
	return "unavailable"
}

// VideoUsage returns public billing information without exposing internal
// quota units, upstream routing, provider costs, or margin data.
func (t *Task) VideoUsage() *dto.VideoUsage {
	currency := "CNY"
	outputTokens := 0
	totalTokens := 0
	billing := t.PrivateData.BillingContext
	if billing == nil || billing.QuotaPerUnit <= 0 {
		return &dto.VideoUsage{Currency: currency, BillingStatus: "unavailable"}
	}
	quotaPerUnit := billing.QuotaPerUnit
	if billing.BillingCurrency != "" {
		currency = billing.BillingCurrency
	}
	outputTokens = billing.CompletionTokens
	totalTokens = billing.TotalTokens
	amount := math.Round(float64(t.Quota)/quotaPerUnit*1_000_000) / 1_000_000
	reservedQuota := billing.ReservedQuota
	if reservedQuota <= 0 {
		reservedQuota = t.Quota
	}
	reservedAmount := math.Round(float64(reservedQuota)/quotaPerUnit*1_000_000) / 1_000_000
	usage := &dto.VideoUsage{
		OutputTokens:  outputTokens,
		TotalTokens:   totalTokens,
		Currency:      currency,
		BillingStatus: billing.BillingStatus,
	}
	switch billing.BillingStatus {
	case "settled", "settled_with_debt":
		usage.ChargedAmount = amount
		usage.SupplementAmount = math.Round(float64(billing.SupplementedQuota)/quotaPerUnit*1_000_000) / 1_000_000
		usage.RefundedAmount = math.Round(float64(billing.RefundedQuota)/quotaPerUnit*1_000_000) / 1_000_000
	case "reserved", "settlement_pending", "payment_required", "pending_review":
		usage.ReservedAmount = reservedAmount
	case "refund_pending":
		usage.ChargedAmount = amount
		usage.PendingRefundAmount = amount
	case "refunded":
		refundedQuota := billing.RefundedQuota
		if refundedQuota <= 0 {
			refundedQuota = t.Quota
		}
		usage.RefundedAmount = math.Round(float64(refundedQuota)/quotaPerUnit*1_000_000) / 1_000_000
	default:
		usage.BillingStatus = "unavailable"
	}
	return usage
}

// ToXingTuVideo renders the stable public contract without provider routing,
// upstream task identifiers, actual provider cost, or margin information.
func (t *Task) ToXingTuVideo() *dto.XingTuVideoResponse {
	response := &dto.XingTuVideoResponse{
		ID:             t.TaskID,
		RequestID:      t.PrivateData.RequestID,
		Object:         "video",
		Model:          t.Properties.OriginModelName,
		Status:         t.xingTuTaskStatus(),
		Progress:       parseTaskProgress(t.Progress),
		CreatedAt:      t.CreatedAt,
		ResultDelivery: t.ResultDeliveryStatus(),
		Billing:        t.xingTuVideoBilling(),
	}
	if t.Status == TaskStatusSuccess || t.Status == TaskStatusFailure {
		response.CompletedAt = t.UpdatedAt
	}
	if response.ResultDelivery == "ready" && t.GetResultURL() != "" {
		baseURL := strings.TrimRight(system_setting.ServerAddress, "/")
		response.Result = &dto.XingTuVideoResult{
			Type: "url",
			URL:  fmt.Sprintf("%s/v1/videos/%s/content", baseURL, url.PathEscape(t.TaskID)),
		}
	}
	if t.Status == TaskStatusFailure {
		response.Error = &dto.OpenAIVideoError{Code: "video_generation_failed", Message: "video generation failed"}
	}
	if ctx := t.PrivateData.BillingContext; ctx != nil && (ctx.TotalTokens > 0 || ctx.CompletionTokens > 0) {
		response.Usage = &dto.XingTuVideoUsage{}
		if ctx.CompletionTokens > 0 {
			value := ctx.CompletionTokens
			response.Usage.OutputTokens = &value
		}
		if ctx.TotalTokens > 0 {
			value := ctx.TotalTokens
			response.Usage.TotalTokens = &value
		}
	}
	return response
}

func (t *Task) xingTuTaskStatus() string {
	if billing := t.PrivateData.BillingContext; billing != nil && billing.BillingStatus == "pending_review" {
		return "pending_review"
	}
	switch t.Status {
	case TaskStatusSuccess:
		return "succeeded"
	case TaskStatusFailure:
		return "failed"
	case TaskStatusInProgress:
		return "running"
	case TaskStatusUnknown:
		return "uncertain"
	case TaskStatusSubmitted:
		return "submitting"
	default:
		return "queued"
	}
}

func parseTaskProgress(progress string) int {
	progress = strings.TrimSuffix(strings.TrimSpace(progress), "%")
	value, _ := strconv.Atoi(progress)
	return value
}

func quotaCNYExact(quota int, quotaPerUnit float64) string {
	units := int64(math.Round(quotaPerUnit))
	if quota <= 0 || units <= 0 || math.Abs(quotaPerUnit-float64(units)) > 0.000001 {
		return "0.000000"
	}
	microCNY := (int64(quota)*1_000_000 + units/2) / units
	return fmt.Sprintf("%d.%06d", microCNY/1_000_000, microCNY%1_000_000)
}

func exactString(value string) *string {
	return &value
}

func (t *Task) xingTuVideoBilling() *dto.XingTuVideoBilling {
	ctx := t.PrivateData.BillingContext
	if ctx == nil {
		return &dto.XingTuVideoBilling{
			ContractVersion: "xtai-video-billing-v2",
			Status:          "unavailable",
			Currency:        "CNY",
			ReserveBasis:    "ark_official_1_5",
			ReservedAmount:  "0.000000",
			Markup:          "1.5",
		}
	}
	currency := ctx.BillingCurrency
	if currency == "" {
		currency = "CNY"
	}
	reservedQuota := ctx.ReservedQuota
	if reservedQuota <= 0 {
		reservedQuota = t.Quota
	}
	publicBilling := &dto.XingTuVideoBilling{
		ContractVersion: ctx.ContractVersion,
		Status:          ctx.BillingStatus,
		Currency:        currency,
		ReserveBasis:    "ark_official_1_5",
		ReservedAmount:  quotaCNYExact(reservedQuota, ctx.QuotaPerUnit),
		Markup:          "1.5",
		PricingRevision: ctx.OfficialPricingRevision,
	}
	switch ctx.BillingStatus {
	case "settled", "settled_with_debt":
		publicBilling.ChargedAmount = exactString(quotaCNYExact(t.Quota, ctx.QuotaPerUnit))
		publicBilling.RefundAmount = exactString(quotaCNYExact(ctx.RefundedQuota, ctx.QuotaPerUnit))
		publicBilling.SupplementAmount = exactString(quotaCNYExact(ctx.SupplementedQuota, ctx.QuotaPerUnit))
		if t.UpdatedAt > 0 {
			beijing := time.FixedZone("Asia/Shanghai", 8*60*60)
			publicBilling.SettledAt = time.Unix(t.UpdatedAt, 0).In(beijing).Format(time.RFC3339)
		}
	case "refunded":
		publicBilling.ChargedAmount = exactString("0.000000")
		publicBilling.RefundAmount = exactString(quotaCNYExact(ctx.RefundedQuota, ctx.QuotaPerUnit))
		publicBilling.SupplementAmount = exactString("0.000000")
	}
	return publicBilling
}
