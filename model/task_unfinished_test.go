package model

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestGetAllUnFinishSyncTasksIncludesNonterminalHundredPercent(t *testing.T) {
	truncateTables(t)

	insertTask(t, &Task{
		TaskID:     "task_processing_100",
		Status:     TaskStatusInProgress,
		Progress:   "100%",
		SubmitTime: 100,
		Data:       json.RawMessage(`{}`),
	})
	insertTask(t, &Task{
		TaskID:     "task_processing_50",
		Status:     TaskStatusInProgress,
		Progress:   "50%",
		SubmitTime: 100,
		Data:       json.RawMessage(`{}`),
	})
	insertTask(t, &Task{
		TaskID:     "task_success_100",
		Status:     TaskStatusSuccess,
		Progress:   "100%",
		SubmitTime: 100,
		Data:       json.RawMessage(`{}`),
	})
	insertTask(t, &Task{
		TaskID:     "task_failure_100",
		Status:     TaskStatusFailure,
		Progress:   "100%",
		SubmitTime: 100,
		Data:       json.RawMessage(`{}`),
	})

	tasks := GetAllUnFinishSyncTasks(10)
	assert.Equal(t, []string{"task_processing_100", "task_processing_50"}, taskIDs(tasks))
}

func TestGetTimedOutUnfinishedTasksIncludesNonterminalHundredPercent(t *testing.T) {
	truncateTables(t)

	insertTask(t, &Task{
		TaskID:     "task_processing_100",
		Status:     TaskStatusInProgress,
		Progress:   "100%",
		SubmitTime: 100,
		Data:       json.RawMessage(`{}`),
	})
	insertTask(t, &Task{
		TaskID:     "task_recent",
		Status:     TaskStatusInProgress,
		Progress:   "100%",
		SubmitTime: 300,
		Data:       json.RawMessage(`{}`),
	})

	tasks := GetTimedOutUnfinishedTasks(200, 10)
	assert.Equal(t, []string{"task_processing_100"}, taskIDs(tasks))
}

func taskIDs(tasks []*Task) []string {
	ids := make([]string, 0, len(tasks))
	for _, task := range tasks {
		ids = append(ids, task.TaskID)
	}
	return ids
}
