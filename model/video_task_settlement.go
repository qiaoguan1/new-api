package model

// VideoTaskSettlement is the private, append-only audit record for one exact
// provider-ledger revision. Provider identifiers and actual upstream cost must
// never be copied into public task DTOs.
type VideoTaskSettlement struct {
	ID                   int64  `json:"-" gorm:"primaryKey;autoIncrement"`
	CreatedAt            int64  `json:"-" gorm:"index"`
	TaskRecordID         int64  `json:"-" gorm:"index;uniqueIndex:idx_video_task_revision,priority:1"`
	TaskID               string `json:"-" gorm:"type:varchar(191);index"`
	SettlementID         string `json:"-" gorm:"type:varchar(64);uniqueIndex"`
	Revision             int    `json:"-" gorm:"uniqueIndex:idx_video_task_revision,priority:2"`
	EvidenceFingerprint  string `json:"-" gorm:"type:varchar(64);uniqueIndex"`
	ProviderTaskID       string `json:"-" gorm:"type:varchar(512)"`
	ActualCostStatus     string `json:"-" gorm:"type:varchar(32)"`
	ActualCostCNYExact   string `json:"-" gorm:"type:varchar(64)"`
	EvidenceSource       string `json:"-" gorm:"type:varchar(64)"`
	EvidenceID           string `json:"-" gorm:"type:varchar(191)"`
	ObservedAt           string `json:"-" gorm:"type:varchar(64)"`
	ChargedQuota         int    `json:"-"`
	SettlementDeltaQuota int    `json:"-"`
}
