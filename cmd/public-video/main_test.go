package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/alicebob/miniredis/v2"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func fixture(t *testing.T) (*server, *gin.Engine, model.User, model.Token) {
	t.Helper()
	t.Setenv("QUOTA_DB_AUTHORITATIVE", "true")
	db, e := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, e)
	model.DB = db
	model.LOG_DB = db
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	sqlDB, e := db.DB()
	require.NoError(t, e)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })
	require.NoError(t, db.AutoMigrate(&model.User{}, &model.Token{}, &model.Log{}, &model.PublicVideoTask{}))
	mr := miniredis.RunT(t)
	common.RDB = redis.NewClient(&redis.Options{Addr: mr.Addr()})
	common.RedisEnabled = true
	common.CryptoSecret = "testing-only"
	t.Cleanup(func() { _ = common.RDB.Close(); common.RedisEnabled = false })
	u := model.User{Username: "public-test", AffCode: "testaff", Password: "unused", Status: 1, Quota: 1000000, Group: "default"}
	require.NoError(t, db.Create(&u).Error)
	token := model.Token{UserId: u.Id, Key: "testing-key", Status: 1, ExpiredTime: -1, RemainQuota: 1000000}
	require.NoError(t, db.Create(&token).Error)
	s := &server{rate: "500000", publicURL: "https://api.example.test", serviceToken: strings.Repeat("x", 32), client: &http.Client{Timeout: time.Second}}
	preflight := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/status" {
			_, _ = w.Write([]byte(`{"data":{"quota_db_authoritative":true,"enable_batch_update":false}}`))
			return
		}
		if r.URL.Path == "/v1/capabilities" {
			_, _ = w.Write([]byte(`{"capabilities":{"video":{"traffic_enabled":true,"models":[{"id":"grok-imagine-video-official","available":true}]}}}`))
			return
		}
		_, _ = w.Write([]byte(`{"pricing":{"models":[{"model":"grok-imagine-video-official","resolution":"480p","currency":"CNY","billing_unit":"output_second","cny_per_second_exact":"0.675000"}]}}`))
	}))
	t.Cleanup(preflight.Close)
	s.backend = preflight.URL
	s.native = preflight.URL
	gin.SetMode(gin.TestMode)
	r := gin.New()
	require.NoError(t, r.SetTrustedProxies(nil))
	r.NoRoute(s.serve)
	return s, r, u, token
}
func request(r http.Handler, method, path, key, body string) *httptest.ResponseRecorder {
	q := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	q.RemoteAddr = "203.0.113.1:1000"
	if key != "" {
		q.Header.Set("Authorization", "Bearer sk-"+key)
	}
	q.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, q)
	return w
}

const validBody = `{"request_id":"test-one","model":"grok-imagine-video-official","prompt":"A blue ball","duration":1,"resolution":"480p"}`

func TestPublicVideoHTTPIsolationAndRestartSettlement(t *testing.T) {
	s, r, u, token := fixture(t)
	first := request(r, "POST", "/v1/videos", token.Key, validBody)
	require.Equal(t, 202, first.Code, first.Body.String())
	var data map[string]interface{}
	require.NoError(t, common.Unmarshal(first.Body.Bytes(), &data))
	id := data["id"].(string)
	replay := request(r, "POST", "/v1/videos", token.Key, validBody)
	assert.Equal(t, 200, replay.Code)
	conflict := request(r, "POST", "/v1/videos", token.Key, strings.Replace(validBody, "blue ball", "red ball", 1))
	assert.Equal(t, 409, conflict.Code)
	other := model.User{Username: "other", AffCode: "otheraff", Password: "unused", Status: 1, Quota: 1000000}
	require.NoError(t, model.DB.Create(&other).Error)
	otherToken := model.Token{UserId: other.Id, Key: "other-key", Status: 1, ExpiredTime: -1, RemainQuota: 1000000}
	require.NoError(t, model.DB.Create(&otherToken).Error)
	assert.Equal(t, 404, request(r, "GET", "/v1/videos/"+id, otherToken.Key, "").Code)
	assert.Equal(t, 404, request(r, "GET", "/v1/videos/"+id+"/content", otherToken.Key, "").Code)
	assert.Equal(t, 404, request(r, "GET", "/v1/operations/provider-health", token.Key, "").Code)
	calls := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		require.Equal(t, "Bearer "+s.serviceToken, q.Header.Get("Authorization"))
		calls++
		if strings.HasPrefix(q.URL.Path, "/v1/video-jobs/by-request/") {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{}`))
			return
		}
		if q.Method == "POST" {
			w.WriteHeader(202)
			_, _ = ioWrite(w, `{"id":"vjob_11111111111111111111111111111111","status":"running","billing":{"status":"reserved"}}`)
			return
		}
		_, _ = ioWrite(w, `{"id":"vjob_11111111111111111111111111111111","status":"succeeded","result_delivery":"ready","result_url":"https://internal.invalid/secret","billing":{"status":"settled","currency":"CNY","charged_amount":"0.450000"}}`)
	}))
	defer backend.Close()
	s.backend = backend.URL
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row, "id = ?", id).Error)
	require.NoError(t, s.process(row))
	require.NoError(t, model.DB.First(&row, "id = ?", id).Error)
	restarted := &server{backend: backend.URL, native: s.native, client: s.client, serviceToken: s.serviceToken, publicURL: s.publicURL, rate: s.rate}
	require.NoError(t, restarted.process(row))
	require.NoError(t, restarted.process(row))
	require.NoError(t, model.DB.First(&u, u.Id).Error)
	assert.Equal(t, 775000, u.Quota)
	assert.Equal(t, 225000, u.UsedQuota)
	require.NoError(t, model.DB.Model(&token).Updates(map[string]interface{}{"status": 4, "remain_quota": 0}).Error)
	done := request(r, "GET", "/v1/videos/"+id, token.Key, "")
	assert.Equal(t, 200, done.Code)
	assert.Contains(t, done.Body.String(), `"charged_amount":"0.450000"`)
	assert.NotContains(t, done.Body.String(), "internal.invalid")
	assert.Equal(t, 4, calls)
}
func ioWrite(w http.ResponseWriter, value string) (int, error) { return w.Write([]byte(value)) }

func TestPublicVideoAuthAndValidation(t *testing.T) {
	_, r, u, token := fixture(t)
	assert.Equal(t, 401, request(r, "POST", "/v1/videos", "wrong", validBody).Code)
	assert.Equal(t, 400, request(r, "POST", "/v1/videos", token.Key, strings.Replace(validBody, "A blue ball", "   ", 1)).Code)
	assert.Equal(t, 400, request(r, "POST", "/v1/videos", token.Key, strings.Replace(validBody, `"duration":1`, `"duration":1.2`, 1)).Code)
	assert.Equal(t, 400, request(r, "POST", "/v1/videos", token.Key, strings.Replace(validBody, `"duration":1`, `"images":["https://example.com/a"],"duration":1`, 1)).Code)
	ips := "198.51.100.1"
	require.NoError(t, model.DB.Model(&token).Update("allow_ips", ips).Error)
	assert.Equal(t, 401, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	require.NoError(t, model.DB.Model(&token).Updates(map[string]interface{}{"allow_ips": "", "model_limits_enabled": true, "model_limits": "other-model"}).Error)
	assert.Equal(t, 403, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	require.NoError(t, model.DB.Model(&token).Update("model_limits_enabled", false).Error)
	require.NoError(t, model.DB.Model(&u).Update("quota", 1).Error)
	assert.Equal(t, 402, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	require.NoError(t, model.DB.Model(&token).Update("status", 2).Error)
	assert.Equal(t, 401, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	var count int64
	require.NoError(t, model.DB.Model(&model.PublicVideoTask{}).Count(&count).Error)
	assert.Zero(t, count)
}
func TestPublicVideoIgnoresStaleRedisQuota(t *testing.T) {
	_, r, u, token := fixture(t)
	ctx := context.Background()
	uk := "user:1"
	tk := "token:" + common.GenerateHMAC(token.Key)
	require.NoError(t, common.RDB.HSet(ctx, uk, "Quota", u.Quota).Err())
	require.NoError(t, common.RDB.HSet(ctx, tk, "RemainQuota", token.RemainQuota).Err())
	require.Equal(t, 202, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	q, e := model.GetUserQuota(u.Id, false)
	require.NoError(t, e)
	assert.Equal(t, 662500, q)
	cached, e := common.RDB.HGet(ctx, uk, "Quota").Int()
	require.NoError(t, e)
	assert.Equal(t, 1000000, cached)
	fromDB, e := model.GetTokenByKey(token.Key, false)
	require.NoError(t, e)
	assert.Equal(t, 662500, fromDB.RemainQuota)
}

func TestPublicVideoReplayRejectionDoesNotAssumeRefund(t *testing.T) {
	s, r, u, token := fixture(t)
	require.Equal(t, 202, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.Method == "GET" {
			w.WriteHeader(404)
		} else {
			w.WriteHeader(400)
		}
		_, _ = w.Write([]byte(`{}`))
	}))
	defer backend.Close()
	s.backend = backend.URL
	var row model.PublicVideoTask
	require.NoError(t, model.DB.First(&row).Error)
	require.NoError(t, s.process(row))
	require.NoError(t, model.DB.First(&row).Error)
	assert.Equal(t, "pending_review", row.State)
	assert.Empty(t, row.ChargedCNY)
	require.NoError(t, model.DB.First(&u, u.Id).Error)
	assert.Equal(t, 662500, u.Quota)
}

func TestPublicVideoNativeRollbackFailsClosedBeforeReservation(t *testing.T) {
	s, r, _, token := fixture(t)
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		_, _ = w.Write([]byte(`{"data":{"quota_db_authoritative":false,"enable_batch_update":true}}`))
	}))
	defer native.Close()
	s.native = native.URL
	assert.Equal(t, 503, request(r, "POST", "/v1/videos", token.Key, validBody).Code)
	var count int64
	require.NoError(t, model.DB.Model(&model.PublicVideoTask{}).Count(&count).Error)
	assert.Zero(t, count)
}
