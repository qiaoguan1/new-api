package model

import (
	"errors"
	"time"
)

const (
	VideoRequestStateClaimed   = "claimed"
	VideoRequestStateCompleted = "completed"
	VideoRequestStateFailed    = "failed"
	VideoRequestStateUncertain = "uncertain"
)

var ErrVideoRequestIdempotencyConflict = errors.New("video request idempotency conflict")

// VideoRequestClaim reserves one downstream request ID before any billing or
// provider side effect. The per-user unique index is portable across all
// supported databases and prevents concurrent duplicate submissions.
type VideoRequestClaim struct {
	ID                 int64  `json:"-" gorm:"primaryKey;autoIncrement"`
	CreatedAt          int64  `json:"-" gorm:"index"`
	UpdatedAt          int64  `json:"-"`
	UserID             int    `json:"-" gorm:"uniqueIndex:idx_video_request_user_key,priority:1"`
	RequestID          string `json:"-" gorm:"type:varchar(128);uniqueIndex:idx_video_request_user_key,priority:2"`
	RequestFingerprint string `json:"-" gorm:"type:varchar(64)"`
	TaskID             string `json:"-" gorm:"type:varchar(191);uniqueIndex"`
	State              string `json:"-" gorm:"type:varchar(32);index"`
	ErrorCode          string `json:"-" gorm:"type:varchar(64)"`
	ErrorMessage       string `json:"-" gorm:"type:text"`
}

// ClaimVideoRequest atomically claims a per-user request ID. A same-payload
// replay returns the existing row; a changed payload fails closed.
func ClaimVideoRequest(userID int, requestID, fingerprint, taskID string) (*VideoRequestClaim, bool, error) {
	now := time.Now().Unix()
	claim := &VideoRequestClaim{
		CreatedAt:          now,
		UpdatedAt:          now,
		UserID:             userID,
		RequestID:          requestID,
		RequestFingerprint: fingerprint,
		TaskID:             taskID,
		State:              VideoRequestStateClaimed,
	}
	if err := DB.Create(claim).Error; err == nil {
		return claim, true, nil
	} else {
		var existing VideoRequestClaim
		if findErr := DB.Where("user_id = ? AND request_id = ?", userID, requestID).First(&existing).Error; findErr != nil {
			return nil, false, err
		}
		if existing.RequestFingerprint != fingerprint {
			return nil, false, ErrVideoRequestIdempotencyConflict
		}
		return &existing, false, nil
	}
}

func CompleteVideoRequestClaim(id int64) error {
	return DB.Model(&VideoRequestClaim{}).Where("id = ?", id).Updates(map[string]interface{}{
		"state":         VideoRequestStateCompleted,
		"updated_at":    time.Now().Unix(),
		"error_code":    "",
		"error_message": "",
	}).Error
}

func FailVideoRequestClaim(id int64, code, message string, uncertain bool) error {
	state := VideoRequestStateFailed
	if uncertain {
		state = VideoRequestStateUncertain
	}
	return DB.Model(&VideoRequestClaim{}).Where("id = ?", id).Updates(map[string]interface{}{
		"state":         state,
		"updated_at":    time.Now().Unix(),
		"error_code":    code,
		"error_message": message,
	}).Error
}
