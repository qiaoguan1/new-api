// public-video adds owner-scoped API-key access without replacing the existing relay server.
package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
)

type videoSpec struct {
	Resolution string
	Duration   int
	Reserve    string
}

var specs = map[string]videoSpec{
	"wan3.0-video": {"480p", 2, "1.125000"}, "wan3.0-video-prime": {"480p", 2, "1.620000"},
	"grok-imagine-1.5-video": {"480p", 6, "1.350000"}, "grok-video-3": {"720p", 6, "1.350000"},
	"grok-imagine-video-official": {"480p", 1, "0.675000"}, "omni-flash": {"720p", 4, "3.825000"}, "flux-3-video": {"720p", 5, "10.687500"},
}

type server struct {
	backend, legacy, native, serviceToken, publicURL, rate string
	client                                                 *http.Client
}
type identity struct {
	user  model.User
	token model.Token
	group string
}

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)

func response(c *gin.Context, status int, code string) {
	c.AbortWithStatusJSON(status, gin.H{"error": gin.H{"code": code, "message": code, "type": "video_error"}})
}

// authenticate intentionally reads the database, avoiding stale authorization caches.
func authenticate(c *gin.Context) (identity, error) {
	var out identity
	key := c.GetHeader("Authorization")
	if !strings.HasPrefix(strings.ToLower(key), "bearer ") {
		return out, errors.New("unauthorized")
	}
	key = strings.TrimPrefix(strings.TrimSpace(key[7:]), "sk-")
	if key == "" || len(key) > 128 || strings.ContainsAny(key, "\r\n\x00") {
		return out, errors.New("unauthorized")
	}
	if e := model.DB.Where(map[string]interface{}{"key": key}).First(&out.token).Error; e != nil {
		return out, errors.New("unauthorized")
	}
	if out.token.Status != common.TokenStatusEnabled && out.token.Status != common.TokenStatusExhausted {
		return out, errors.New("token_disabled")
	}
	if out.token.ExpiredTime != -1 && out.token.ExpiredTime < time.Now().Unix() {
		return out, errors.New("token_expired")
	}
	if e := model.DB.First(&out.user, out.token.UserId).Error; e != nil || out.user.Status != common.UserStatusEnabled {
		return out, errors.New("user_disabled")
	}
	if ips := out.token.GetIpLimits(); len(ips) > 0 && !common.IsIpInCIDRList(net.ParseIP(c.ClientIP()), ips) {
		return out, errors.New("ip_not_allowed")
	}
	out.group = out.user.Group
	if out.token.Group != "" {
		if !service.GroupInUserUsableGroups(out.user.Group, out.token.Group) {
			return out, errors.New("group_denied")
		}
		if out.token.Group != "auto" && !ratio_setting.ContainsGroupRatio(out.token.Group) {
			return out, errors.New("group_disabled")
		}
		out.group = out.token.Group
	}
	return out, nil
}

func (s *server) backendJSON(method, path string, body []byte) (map[string]interface{}, int, error) {
	req, e := http.NewRequest(method, s.backend+path, bytes.NewReader(body))
	if e != nil {
		return nil, 0, e
	}
	req.Header.Set("Authorization", "Bearer "+s.serviceToken)
	req.Header.Set("X-XingTu-Contract-Version", "xtai-video-billing-v2.2")
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
		var data map[string]interface{}
		if e = common.Unmarshal(body, &data); e != nil {
			return nil, 0, e
		}
		rid, _ := data["request_id"].(string)
		req.Header.Set("Idempotency-Key", rid)
	}
	r, e := s.client.Do(req)
	if e != nil {
		return nil, 0, e
	}
	defer r.Body.Close()
	raw, e := io.ReadAll(io.LimitReader(r.Body, 1024*1024+1))
	if e != nil || len(raw) > 1024*1024 {
		return nil, r.StatusCode, errors.New("gateway_response_invalid")
	}
	var result map[string]interface{}
	e = common.Unmarshal(raw, &result)
	return result, r.StatusCode, e
}

func (s *server) submit(c *gin.Context, who identity) {
	raw, e := io.ReadAll(io.LimitReader(c.Request.Body, 262145))
	if e != nil || len(raw) > 262144 {
		response(c, 413, "request_too_large")
		return
	}
	var body map[string]interface{}
	if common.Unmarshal(raw, &body) != nil || body == nil {
		response(c, 400, "invalid_json")
		return
	}
	name, _ := body["model"].(string)
	spec, ok := specs[name]
	if !ok {
		response(c, 400, "video_model_not_enabled")
		return
	}
	if who.token.ModelLimitsEnabled && !who.token.GetModelLimitsMap()[name] {
		response(c, 403, "model_not_allowed")
		return
	}
	for field := range body {
		if !map[string]bool{"model": true, "prompt": true, "resolution": true, "duration": true, "aspect_ratio": true, "generate_audio": true, "request_id": true, "provider_id": true}[field] {
			response(c, 400, "unsupported_field_"+field)
			return
		}
	}
	prompt, _ := body["prompt"].(string)
	duration, _ := body["duration"].(float64)
	resolution, _ := body["resolution"].(string)
	if len([]rune(prompt)) == 0 || len([]rune(prompt)) > 2500 || duration != float64(spec.Duration) || resolution != spec.Resolution {
		response(c, 400, "unverified_video_spec")
		return
	}
	if aspect, exists := body["aspect_ratio"]; exists && aspect != "16:9" {
		response(c, 400, "unverified_aspect_ratio")
		return
	}
	if audio, exists := body["generate_audio"]; exists && audio != true {
		response(c, 400, "audio_required")
		return
	}
	requestID, _ := body["request_id"].(string)
	header := c.GetHeader("Idempotency-Key")
	if requestID != "" && header != "" && requestID != header {
		response(c, 409, "idempotency_key_mismatch")
		return
	}
	if requestID == "" {
		requestID = header
	}
	if requestID == "" {
		requestID = uuid.NewString()
	}
	if !idPattern.MatchString(requestID) {
		response(c, 400, "invalid_request_id")
		return
	}
	body["generate_audio"] = true
	body["aspect_ratio"] = "16:9"
	body["provider_id"] = "video-aixingtu-api"
	delete(body, "request_id")
	canonical, _ := common.Marshal(body)
	fingerprint := sha256.Sum256(canonical)
	idHash := sha256.Sum256([]byte(fmt.Sprintf("%d:%s", who.user.Id, requestID)))
	id := "vjob_" + hex.EncodeToString(idHash[:16])
	var existing model.PublicVideoTask
	lookup := model.DB.Where("id = ? AND user_id = ?", id, who.user.Id).First(&existing).Error
	if errors.Is(lookup, gorm.ErrRecordNotFound) {
		if e = s.preflight(name, spec); e != nil {
			response(c, 503, "video_price_or_capability_not_ready")
			return
		}
	} else if lookup != nil {
		response(c, 503, "wallet_unavailable")
		return
	}
	body["request_id"] = "public-" + hex.EncodeToString(idHash[:])
	wire, _ := common.Marshal(body)
	row, reused, e := model.ReservePublicVideo(model.PublicVideoTask{ID: id, UserID: who.user.Id, TokenID: who.token.Id, ClientRequestID: requestID, Fingerprint: hex.EncodeToString(fingerprint[:]), Model: name, Group: who.group, Body: string(wire), ReservedCNY: spec.Reserve, QuotaPerCNY: s.rate})
	if e != nil {
		code := 503
		reason := "wallet_unavailable"
		if errors.Is(e, model.ErrPublicVideoQuota) {
			code = 402
			reason = "insufficient_quota"
		}
		if errors.Is(e, model.ErrPublicVideoConflict) {
			code = 409
			reason = "idempotency_conflict"
		}
		if errors.Is(e, model.ErrPublicVideoForbidden) {
			code = 403
			reason = "access_denied"
		}
		response(c, code, reason)
		return
	}
	if e = model.FlushPublicVideoCache(row.ID); e != nil {
		response(c, 503, "reservation_pending_retry_same_id")
		return
	}
	status := 202
	if reused {
		status = 200
	}
	c.JSON(status, s.snapshot(row))
}

// preflight refuses a new reservation if the execution catalog or its exact price changed.
func (s *server) preflight(name string, spec videoSpec) error {
	caps, status, e := s.backendJSON("GET", "/v1/capabilities", nil)
	if e != nil || status != 200 {
		return errors.New("catalog_unavailable")
	}
	c, _ := caps["capabilities"].(map[string]interface{})
	video, _ := c["video"].(map[string]interface{})
	rows, _ := video["models"].([]interface{})
	ready := false
	for _, value := range rows {
		row, ok := value.(map[string]interface{})
		if ok && row["id"] == name && row["available"] == true {
			ready = true
		}
	}
	if !ready || video["traffic_enabled"] != true {
		return errors.New("model_unavailable")
	}
	prices, status, e := s.backendJSON("GET", "/v1/video-prices", nil)
	if e != nil || status != 200 {
		return errors.New("price_unavailable")
	}
	pricing, _ := prices["pricing"].(map[string]interface{})
	rows, _ = pricing["models"].([]interface{})
	for _, value := range rows {
		row, ok := value.(map[string]interface{})
		if !ok || row["model"] != name || row["resolution"] != spec.Resolution {
			continue
		}
		unit, _ := row["cny_per_second_exact"].(string)
		rate, e := decimal.NewFromString(unit)
		want, _ := decimal.NewFromString(spec.Reserve)
		if e == nil && rate.Mul(decimal.NewFromInt(int64(spec.Duration))).Equal(want) && row["currency"] == "CNY" && row["billing_unit"] == "output_second" {
			return nil
		}
	}
	return errors.New("price_mismatch")
}

func (s *server) snapshot(row model.PublicVideoTask) map[string]interface{} {
	data := map[string]interface{}{"id": row.ID, "object": "video", "request_id": row.ClientRequestID, "model": row.Model, "status": "queued", "created_at": row.CreatedAt, "result_delivery": "unavailable", "result_url": nil}
	if row.Snapshot != "" {
		_ = common.UnmarshalJsonStr(row.Snapshot, &data)
	}
	data["id"] = row.ID
	data["request_id"] = row.ClientRequestID
	data["model"] = row.Model
	billing := map[string]interface{}{"contract_version": "xtai-video-billing-v2.2", "status": "reserved", "currency": "CNY", "reserve_basis": "verified_upstream_1_5", "reserved_amount": row.ReservedCNY, "charged_amount": nil, "refund_amount": nil, "supplement_amount": nil}
	if row.State == "settled" {
		a, _ := decimal.NewFromString(row.ChargedCNY)
		r, _ := decimal.NewFromString(row.ReservedCNY)
		billing["status"] = "settled"
		billing["charged_amount"] = row.ChargedCNY
		billing["refund_amount"] = decimal.Max(r.Sub(a), decimal.Zero).StringFixed(6)
		billing["supplement_amount"] = decimal.Max(a.Sub(r), decimal.Zero).StringFixed(6)
		if data["status"] == "succeeded" {
			data["result_delivery"] = "ready"
			data["result_url"] = s.publicURL + "/v1/videos/" + row.ID + "/content"
			data["result"] = map[string]interface{}{"type": "url", "url": data["result_url"]}
		}
	} else {
		data["result"] = nil
		data["result_url"] = nil
		if data["status"] == "succeeded" {
			billing["status"] = "settlement_pending"
			data["result_delivery"] = "pending_settlement"
		}
		if row.State == "pending_review" {
			billing["status"] = "pending_review"
			data["status"] = "pending_review"
		}
	}
	data["billing"] = billing
	return data
}

func (s *server) process(row model.PublicVideoTask) error {
	if e := model.FlushPublicVideoCache(row.ID); e != nil {
		return e
	}
	var data map[string]interface{}
	var status int
	var e error
	if row.BackendID == "" {
		// Repeating the SAME request to our durable gateway is safe; it never repeats unknown provider submits.
		data, status, e = s.backendJSON("POST", "/v1/videos", []byte(row.Body))
		if e != nil {
			return e
		}
		if status != 200 && status != 202 {
			// Only contract-validation rejects prove no task was created. Other failures remain reserved.
			if status == 400 || status == 413 {
				raw, _ := common.Marshal(map[string]interface{}{"status": "failed", "error": map[string]string{"code": "gateway_validation_rejected"}})
				return model.SettlePublicVideo(row.ID, "0.000000", string(raw))
			}
			return fmt.Errorf("gateway_submit_http_%d", status)
		}
		backendID, _ := data["id"].(string)
		if !regexp.MustCompile(`^vjob_[0-9a-f]{32}$`).MatchString(backendID) {
			return errors.New("gateway_identity_pending")
		}
		row.BackendID = backendID
		if e = model.DB.Model(&row).Where("state IN ? AND (backend_id = ? OR backend_id = ?)", []string{"reserved", "submitted", "pending_review"}, "", backendID).Updates(map[string]interface{}{"backend_id": backendID, "state": "submitted"}).Error; e != nil {
			return e
		}
	} else {
		data, status, e = s.backendJSON("GET", "/v1/videos/"+row.BackendID, nil)
		if e != nil {
			return e
		}
		if status != 200 {
			return fmt.Errorf("gateway_query_http_%d", status)
		}
	}
	raw, _ := common.Marshal(data)
	billing, _ := data["billing"].(map[string]interface{})
	if billing["status"] == "settled" || billing["status"] == "refunded" {
		amount, ok := billing["charged_amount"].(string)
		if !ok || billing["currency"] != "CNY" {
			return errors.New("gateway_settlement_invalid")
		}
		if e = model.SettlePublicVideo(row.ID, amount, string(raw)); e != nil {
			return e
		}
		return model.FlushPublicVideoCache(row.ID)
	}
	state := "submitted"
	if billing["status"] == "pending_review" {
		state = "pending_review"
	}
	return model.DB.Model(&row).Where("state IN ?", []string{"reserved", "submitted", "pending_review"}).Updates(map[string]interface{}{"state": state, "snapshot": string(raw), "updated_at": time.Now().Unix(), "last_error": ""}).Error
}

func (s *server) worker() {
	for range time.Tick(3 * time.Second) {
		if e := model.FlushPublicVideoCache(""); e != nil {
			log.Print("public video cache delivery pending")
		}
		var rows []model.PublicVideoTask
		if model.DB.Where("state IN ?", []string{"reserved", "submitted", "pending_review"}).Order("updated_at asc").Limit(30).Find(&rows).Error != nil {
			continue
		}
		var workers sync.WaitGroup
		slots := make(chan struct{}, 4)
		for _, row := range rows {
			slots <- struct{}{}
			workers.Add(1)
			go func(row model.PublicVideoTask) {
				defer workers.Done()
				defer func() { <-slots }()
				if e := s.process(row); e != nil {
					model.DB.Model(&row).Where("state <> ?", "settled").Updates(map[string]interface{}{"last_error": "reconciliation_pending", "updated_at": time.Now().Unix()})
				}
			}(row)
		}
		workers.Wait()
	}
}

func (s *server) serve(c *gin.Context) {
	if c.Request.URL.Path == "/health" {
		c.JSON(200, gin.H{"ok": true})
		return
	}
	if subtle.ConstantTimeCompare([]byte(c.GetHeader("Authorization")), []byte("Bearer "+s.serviceToken)) == 1 {
		target, _ := url.Parse(s.legacy)
		httputil.NewSingleHostReverseProxy(target).ServeHTTP(c.Writer, c.Request)
		return
	}
	who, e := authenticate(c)
	if e != nil {
		response(c, 401, e.Error())
		return
	}
	path := c.Request.URL.Path
	if path == "/v1/models" && c.Request.Method == "GET" {
		items := []interface{}{}
		req, _ := http.NewRequest("GET", s.native+"/v1/models", nil)
		req.Header.Set("Authorization", c.GetHeader("Authorization"))
		req.Header.Set("X-Forwarded-For", c.ClientIP())
		if r, e := s.client.Do(req); e == nil {
			defer r.Body.Close()
			var data map[string]interface{}
			if r.StatusCode == 200 && common.DecodeJson(io.LimitReader(r.Body, 1024*1024), &data) == nil {
				items, _ = data["data"].([]interface{})
			}
		}
		seen := map[string]bool{}
		for _, v := range items {
			if m, ok := v.(map[string]interface{}); ok {
				if id, ok := m["id"].(string); ok {
					seen[id] = true
				}
			}
		}
		for name := range specs {
			if !seen[name] && (!who.token.ModelLimitsEnabled || who.token.GetModelLimitsMap()[name]) {
				items = append(items, gin.H{"id": name, "object": "model", "owned_by": "xingtu", "created": 1790176800})
			}
		}
		c.JSON(200, gin.H{"object": "list", "data": items})
		return
	}
	if path == "/v1/videos" && c.Request.Method == "POST" {
		s.submit(c, who)
		return
	}
	if c.Request.Method != "GET" && c.Request.Method != "HEAD" {
		response(c, 405, "method_not_allowed")
		return
	}
	if path == "/v1/capabilities" || path == "/v1/video-prices" {
		data, status, e := s.backendJSON("GET", path, nil)
		if e != nil || status != 200 {
			response(c, 503, "catalog_unavailable")
			return
		}
		var parent map[string]interface{}
		field := "models"
		if path == "/v1/capabilities" {
			caps, _ := data["capabilities"].(map[string]interface{})
			parent, _ = caps["video"].(map[string]interface{})
		} else {
			parent, _ = data["pricing"].(map[string]interface{})
		}
		rows, _ := parent[field].([]interface{})
		if parent == nil || rows == nil {
			response(c, 503, "catalog_schema_invalid")
			return
		}
		filtered := []interface{}{}
		for _, v := range rows {
			m, ok := v.(map[string]interface{})
			if !ok {
				continue
			}
			name, _ := m["id"].(string)
			if name == "" {
				name, _ = m["model"].(string)
			}
			if _, ok = specs[name]; ok && (!who.token.ModelLimitsEnabled || who.token.GetModelLimitsMap()[name]) {
				filtered = append(filtered, m)
			}
		}
		parent[field] = filtered
		c.JSON(200, data)
		return
	}
	parts := strings.Split(strings.TrimPrefix(path, "/v1/videos/"), "/")
	if !strings.HasPrefix(path, "/v1/videos/") || len(parts) > 2 || (len(parts) == 2 && parts[1] != "content") {
		response(c, 404, "not_found")
		return
	}
	var row model.PublicVideoTask
	if model.DB.Where("id = ? AND user_id = ?", parts[0], who.user.Id).First(&row).Error != nil {
		response(c, 404, "task_not_found")
		return
	}
	if who.token.ModelLimitsEnabled && !who.token.GetModelLimitsMap()[row.Model] {
		response(c, 403, "model_not_allowed")
		return
	}
	if len(parts) == 1 {
		c.JSON(200, s.snapshot(row))
		return
	}
	if row.State != "settled" {
		response(c, 409, "settlement_pending")
		return
	}
	data := s.snapshot(row)
	if data["status"] != "succeeded" {
		response(c, 404, "result_unavailable")
		return
	}
	req, _ := http.NewRequestWithContext(c.Request.Context(), c.Request.Method, s.backend+"/v1/videos/"+row.BackendID+"/content", nil)
	req.Header.Set("Authorization", "Bearer "+s.serviceToken)
	req.Header.Set("X-XingTu-Contract-Version", "xtai-video-billing-v2.2")
	req.Header.Set("Range", c.GetHeader("Range"))
	r, e := s.client.Do(req)
	if e != nil {
		response(c, 503, "content_unavailable")
		return
	}
	defer r.Body.Close()
	for _, key := range []string{"Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"} {
		if v := r.Header.Get(key); v != "" {
			c.Header(key, v)
		}
	}
	c.Header("Cache-Control", "private, no-store")
	c.Status(r.StatusCode)
	if c.Request.Method != "HEAD" {
		_, _ = io.Copy(c.Writer, r.Body)
	}
}

func main() {
	// Do not deploy this prototype against shared production wallets: see issue173.
	if os.Getenv("PUBLIC_VIDEO_DEVELOPMENT_ONLY") != "true" {
		log.Fatal("public-video prototype blocked pending native wallet integration review")
	}
	common.InitEnv()
	common.IsMasterNode = false
	if os.Getenv("LOG_SQL_DSN") != "" {
		log.Fatal("separate log database unsupported for atomic public-video ledger")
	}
	if e := model.InitDB(); e != nil {
		log.Fatal("database unavailable")
	}
	model.LOG_DB = model.DB
	if e := model.DB.AutoMigrate(&model.PublicVideoTask{}, &model.PublicVideoCacheEvent{}); e != nil {
		log.Fatal("video ledger migration failed")
	}
	model.InitOptionMap()
	if e := common.InitRedisClient(); e != nil || !common.RedisEnabled {
		log.Fatal("redis required")
	}
	s := &server{backend: os.Getenv("PUBLIC_VIDEO_BACKEND"), legacy: os.Getenv("PUBLIC_VIDEO_LEGACY"), native: os.Getenv("PUBLIC_VIDEO_NATIVE"), serviceToken: os.Getenv("VIDEO_JOB_GATEWAY_TOKEN"), publicURL: os.Getenv("PUBLIC_VIDEO_BASE_URL"), rate: os.Getenv("PUBLIC_VIDEO_QUOTA_PER_CNY"), client: &http.Client{Timeout: 60 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	if s.backend == "" || s.legacy == "" || len(s.serviceToken) < 24 || !strings.HasPrefix(s.publicURL, "https://") {
		log.Fatal("public video config incomplete")
	}
	if _, e := model.PublicVideoQuota("1", s.rate); e != nil {
		log.Fatal("invalid frozen wallet conversion")
	}
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())
	if e := r.SetTrustedProxies(strings.Split(os.Getenv("PUBLIC_VIDEO_TRUSTED_PROXIES"), ",")); e != nil {
		log.Fatal(e)
	}
	r.Use(func(c *gin.Context) {
		c.Header("Cache-Control", "no-store")
		c.Header("X-Content-Type-Options", "nosniff")
	})
	r.NoRoute(s.serve)
	go s.worker()
	go model.SyncOptions(60)
	httpServer := &http.Server{Addr: ":8098", Handler: r, ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 120 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	log.Fatal(httpServer.ListenAndServe())
}
