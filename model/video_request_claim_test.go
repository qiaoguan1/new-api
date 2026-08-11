package model

import (
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestClaimVideoRequestIsDurableAndRejectsChangedPayload(t *testing.T) {
	truncateTables(t)

	first, created, err := ClaimVideoRequest(7, "req_stable", "fingerprint-a", "task_public_a")
	require.NoError(t, err)
	require.True(t, created)
	require.Equal(t, "task_public_a", first.TaskID)
	require.Equal(t, VideoRequestStateClaimed, first.State)

	replay, created, err := ClaimVideoRequest(7, "req_stable", "fingerprint-a", "task_should_not_be_used")
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, first.ID, replay.ID)
	require.Equal(t, "task_public_a", replay.TaskID)

	_, created, err = ClaimVideoRequest(7, "req_stable", "fingerprint-b", "task_other")
	require.False(t, created)
	require.True(t, errors.Is(err, ErrVideoRequestIdempotencyConflict))
}

func TestCompleteVideoRequestClaimKeepsReplayLinkedToPublicTask(t *testing.T) {
	truncateTables(t)

	claim, created, err := ClaimVideoRequest(9, "req_complete", "fingerprint", "task_public")
	require.NoError(t, err)
	require.True(t, created)
	require.NoError(t, CompleteVideoRequestClaim(claim.ID))

	replay, created, err := ClaimVideoRequest(9, "req_complete", "fingerprint", "task_unused")
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, VideoRequestStateCompleted, replay.State)
	require.Equal(t, "task_public", replay.TaskID)
}

func TestFailVideoRequestClaimPersistsSafeTerminalState(t *testing.T) {
	truncateTables(t)

	claim, created, err := ClaimVideoRequest(11, "req_failed", "fingerprint", "task_failed")
	require.NoError(t, err)
	require.True(t, created)
	require.NoError(t, FailVideoRequestClaim(claim.ID, "invalid_video_request", "video request was rejected", false))

	replay, created, err := ClaimVideoRequest(11, "req_failed", "fingerprint", "task_unused")
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, VideoRequestStateFailed, replay.State)
	require.Equal(t, "invalid_video_request", replay.ErrorCode)
	require.Equal(t, "video request was rejected", replay.ErrorMessage)
}

func TestReopenVideoRequestClaimAllowsOneRetryAfterRecharge(t *testing.T) {
	truncateTables(t)

	claim, created, err := ClaimVideoRequest(12, "req_recharge", "fingerprint", "task_recharge")
	require.NoError(t, err)
	require.True(t, created)
	require.NoError(t, FailVideoRequestClaim(claim.ID, "insufficient_user_quota", "recharge required", false))

	reopened, err := ReopenVideoRequestClaim(claim.ID, "insufficient_user_quota")
	require.NoError(t, err)
	require.True(t, reopened)
	reopened, err = ReopenVideoRequestClaim(claim.ID, "insufficient_user_quota")
	require.NoError(t, err)
	require.False(t, reopened)

	replay, created, err := ClaimVideoRequest(12, "req_recharge", "fingerprint", "task_unused")
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, VideoRequestStateClaimed, replay.State)
	require.Empty(t, replay.ErrorCode)
}
