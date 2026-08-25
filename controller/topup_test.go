package controller

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func setupTopUpControllerTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	gin.SetMode(gin.TestMode)
	previousSQLite := common.UsingSQLite
	previousMySQL := common.UsingMySQL
	previousPostgreSQL := common.UsingPostgreSQL
	common.UsingSQLite = true
	common.UsingMySQL = false
	common.UsingPostgreSQL = false
	dsn := fmt.Sprintf("file:%s?mode=memory&cache=shared", strings.ReplaceAll(t.Name(), "/", "_"))
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	previousDB := model.DB
	previousLogDB := model.LOG_DB
	model.DB = db
	model.LOG_DB = db
	require.NoError(t, db.AutoMigrate(&model.TopUp{}))
	t.Cleanup(func() {
		common.UsingSQLite = previousSQLite
		common.UsingMySQL = previousMySQL
		common.UsingPostgreSQL = previousPostgreSQL
		model.DB = previousDB
		model.LOG_DB = previousLogDB
		sqlDB, dbErr := db.DB()
		if dbErr == nil {
			_ = sqlDB.Close()
		}
	})
	return db
}

func TestGetUserTopUpsInvokesReconciliationForCurrentPageAndStillResponds(t *testing.T) {
	db := setupTopUpControllerTestDB(t)
	require.NoError(t, db.Create(pendingWechatTopUp(7, "WX-LIST-1")).Error)
	require.NoError(t, db.Create(pendingWechatTopUp(8, "WX-OTHER-USER")).Error)
	called := false
	var receivedUserID int
	var receivedCount int
	reconciler := func(
		ctx context.Context,
		userID int,
		topups []*model.TopUp,
		clientIP string,
	) {
		called = true
		receivedUserID = userID
		receivedCount = len(topups)
	}
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodGet, "/api/user/topup/self?p=0&size=20", nil)
	c.Set("id", 7)

	getUserTopUps(c, reconciler)

	require.True(t, called)
	require.Equal(t, 7, receivedUserID)
	require.Equal(t, 1, receivedCount)
	require.Equal(t, http.StatusOK, recorder.Code)
}
