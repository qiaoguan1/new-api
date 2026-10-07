package controller

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFable51ValidationReturns400WithoutChangingOtherModels(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		name, path, body, originalModel string
		format                          types.RelayFormat
		wantStatus                      int
	}{
		{"Fable wrong protocol field", "/v1/chat/completions", `{"model":"claude-fable-5-1","messages":[{"role":"user","content":"hello"}],"output_config":{"effort":"max"}}`, "", types.RelayFormatOpenAI, http.StatusBadRequest},
		{"Fable negative token decode error", "/v1/chat/completions", `{"model":"claude-fable-5-1","messages":[{"role":"user","content":"hello"}],"max_tokens":-1}`, "", types.RelayFormatOpenAI, http.StatusBadRequest},
		{"Fable missing messages", "/v1/chat/completions", `{"model":"claude-fable-5-1"}`, "", types.RelayFormatOpenAI, http.StatusBadRequest},
		{"Fable native unsupported thinking", "/v1/messages", `{"model":"claude-fable-5-1","max_tokens":8192,"messages":[{"role":"user","content":"hello"}],"thinking":{"type":"disabled"}}`, "", types.RelayFormatClaude, http.StatusBadRequest},
		{"Fable unsupported Responses", "/v1/responses", `{"model":"claude-fable-5-1","input":"hello"}`, "", types.RelayFormatOpenAIResponses, http.StatusBadRequest},
		{"Fable routing marker survives malformed JSON", "/v1/chat/completions", `{"model":"claude-fable-5-1"`, "claude-fable-5-1", types.RelayFormatOpenAI, http.StatusBadRequest},
		{"other missing messages keeps legacy status", "/v1/chat/completions", `{"model":"claude-sonnet-4-5"}`, "", types.RelayFormatOpenAI, http.StatusInternalServerError},
		{"other negative tokens keep legacy status", "/v1/chat/completions", `{"model":"claude-sonnet-4-5","max_tokens":-1}`, "", types.RelayFormatOpenAI, http.StatusInternalServerError},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			c, _ := gin.CreateTestContext(recorder)
			if tc.originalModel != "" {
				c.Set("original_model", tc.originalModel)
			}
			c.Request = httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(tc.body))
			c.Request.Header.Set("Content-Type", "application/json")
			Relay(c, tc.format)
			assert.Equal(t, tc.wantStatus, recorder.Code)
			var response map[string]interface{}
			require.NoError(t, common.Unmarshal(recorder.Body.Bytes(), &response))
			assert.Contains(t, response, "error")
		})
	}
}
