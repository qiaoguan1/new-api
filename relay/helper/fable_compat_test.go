package helper

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFable51ValidationBeforeBilling(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		name, path, model, extra, wantErr string
	}{
		{"chat default", "/v1/chat/completions", "claude-fable-5-1", "", ""},
		{"chat max effort", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":"max"`, ""},
		{"chat wrong output config", "/v1/chat/completions", "claude-fable-5-1", `,"output_config":{"effort":"max"}`, "output_config"},
		{"chat null output config", "/v1/chat/completions", "claude-fable-5-1", `,"output_config":null`, "output_config"},
		{"chat unsupported effort", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":"none"`, "reasoning_effort"},
		{"chat empty effort", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":""`, "reasoning_effort"},
		{"chat null effort", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":null`, "reasoning_effort"},
		{"chat duplicate effort max then low", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":"max","reasoning_effort":"low"`, "duplicate"},
		{"chat duplicate effort low then max", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning_effort":"low","reasoning_effort":"max"`, "duplicate"},
		{"chat noncanonical effort key", "/v1/chat/completions", "claude-fable-5-1", `,"Reasoning_Effort":"max"`, "reasoning_effort"},
		{"chat noncanonical output config key", "/v1/chat/completions", "claude-fable-5-1", `,"Output_Config":{"effort":"max"}`, "output_config"},
		{"duplicate model", "/v1/chat/completions", "claude-fable-5-1", `,"model":"claude-fable-5-1"`, "duplicate"},
		{"chat foreign thinking flag", "/v1/chat/completions", "claude-fable-5-1", `,"enable_thinking":false`, "enable_thinking"},
		{"chat legacy thinking", "/v1/chat/completions", "claude-fable-5-1", `,"thinking":{"type":"enabled","budget_tokens":2048}`, "thinking"},
		{"chat openrouter budget", "/v1/chat/completions", "claude-fable-5-1", `,"reasoning":{"max_tokens":2048}`, "reasoning"},
		{"native default", "/v1/messages", "claude-fable-5-1", "", ""},
		{"native adaptive", "/v1/messages", "claude-fable-5-1", `,"thinking":{"type":"adaptive"}`, ""},
		{"native max effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"max"}`, ""},
		{"native low effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"low"}`, ""},
		{"native medium effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"medium"}`, ""},
		{"native high effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"high"}`, ""},
		{"native config fields preserved", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"max","format":{"type":"json_schema"}}`, ""},
		{"native disabled", "/v1/messages", "claude-fable-5-1", `,"thinking":{"type":"disabled"}`, "thinking"},
		{"native enabled", "/v1/messages", "claude-fable-5-1", `,"thinking":{"type":"enabled","budget_tokens":2048}`, "thinking"},
		{"native adaptive budget", "/v1/messages", "claude-fable-5-1", `,"thinking":{"type":"adaptive","budget_tokens":2048}`, "budget_tokens"},
		{"native invalid effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"minimal"}`, "effort"},
		{"native malformed effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":3}`, "effort"},
		{"native duplicate output config", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"max"},"output_config":{"effort":"low"}`, "duplicate"},
		{"native duplicate effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":"max","effort":"low"}`, "duplicate"},
		{"native noncanonical effort key", "/v1/messages", "claude-fable-5-1", `,"output_config":{"Effort":"max"}`, "effort"},
		{"native duplicate thinking type", "/v1/messages", "claude-fable-5-1", `,"thinking":{"type":"disabled","type":"adaptive"}`, "duplicate"},
		{"native nonobject config", "/v1/messages", "claude-fable-5-1", `,"output_config":[]`, "output_config"},
		{"native null effort", "/v1/messages", "claude-fable-5-1", `,"output_config":{"effort":null}`, "effort"},
		{"native wrong chat effort", "/v1/messages", "claude-fable-5-1", `,"reasoning_effort":"max"`, "reasoning_effort"},
		{"native rejects legacy completion token field", "/v1/messages", "claude-fable-5-1", `,"max_tokens_to_sample":8192`, "max_tokens_to_sample"},
		{"other chat unchanged", "/v1/chat/completions", "claude-fable-5", `,"output_config":{"effort":"max"}`, ""},
		{"other native unchanged", "/v1/messages", "claude-sonnet-4-5", `,"thinking":{"type":"enabled","budget_tokens":2048}`, ""},
		{"other native legacy token field unchanged", "/v1/messages", "claude-sonnet-4-5", `,"max_tokens_to_sample":8192`, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := fmt.Sprintf(`{"model":%q,"max_tokens":8192,"messages":[{"role":"user","content":"hello"}]%s}`, tc.model, tc.extra)
			c, _ := gin.CreateTestContext(httptest.NewRecorder())
			c.Request = httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(body))
			c.Request.Header.Set("Content-Type", "application/json")
			format := types.RelayFormatOpenAI
			if tc.path == "/v1/messages" {
				format = types.RelayFormatClaude
			}
			request, err := GetAndValidateRequest(c, format)
			if tc.wantErr != "" {
				require.ErrorContains(t, err, tc.wantErr)
				assert.Nil(t, request)
				return
			}
			require.NoError(t, err)
			if tc.path == "/v1/messages" && tc.model == "claude-fable-5-1" {
				native, ok := request.(*dto.ClaudeRequest)
				require.True(t, ok)
				require.NotNil(t, native.Thinking)
				assert.Equal(t, "adaptive", native.Thinking.Type)
				assert.Nil(t, native.Thinking.BudgetTokens)
				if strings.Contains(tc.extra, "output_config") {
					assert.JSONEq(t, strings.TrimPrefix(tc.extra, `,"output_config":`), string(native.OutputConfig))
				}
			}
		})
	}
}

func TestFable51RejectsRoutingModelDuplicateBeforeBilling(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("original_model", "claude-fable-5-1")
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(
		`{"model":"claude-fable-5-1","model":"claude-sonnet-4-5","messages":[{"role":"user","content":"hello"}]}`))
	c.Request.Header.Set("Content-Type", "application/json")
	request, err := GetAndValidateRequest(c, types.RelayFormatOpenAI)
	require.ErrorContains(t, err, "duplicate model")
	assert.Nil(t, request)
}

func TestFable51TokenBoundsBeforeBilling(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		name, path, model, params, wantErr string
	}{
		{"chat omitted tokens keep default", "/v1/chat/completions", "claude-fable-5-1", "", ""},
		{"chat minimum tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":1`, ""},
		{"chat maximum tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":128000`, ""},
		{"chat zero tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":0`, "max_tokens"},
		{"chat null tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":null`, "max_tokens"},
		{"chat negative tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":-1`, "max_tokens"},
		{"chat oversized tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":128001`, "max_tokens"},
		{"chat minimum completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":1`, ""},
		{"chat maximum completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":128000`, ""},
		{"chat zero completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":0`, "max_completion_tokens"},
		{"chat null completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":null`, "max_completion_tokens"},
		{"chat negative completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":-1`, "max_completion_tokens"},
		{"chat oversized completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":128001`, "max_completion_tokens"},
		{"chat invalid secondary token limit", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":0`, "max_completion_tokens"},
		{"chat conflicting token aliases rejected", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":128`, "max_completion_tokens"},
		{"chat duplicate tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_tokens":128000,"max_tokens":1`, "duplicate max_tokens"},
		{"chat duplicate completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"max_completion_tokens":128000,"max_completion_tokens":1`, "duplicate max_completion_tokens"},
		{"chat noncanonical tokens", "/v1/chat/completions", "claude-fable-5-1", `,"Max_Tokens":100`, "max_tokens"},
		{"chat noncanonical completion tokens", "/v1/chat/completions", "claude-fable-5-1", `,"Max_Completion_Tokens":100`, "max_completion_tokens"},
		{"native required tokens", "/v1/messages", "claude-fable-5-1", "", "max_tokens is required"},
		{"native wrong completion-only limit", "/v1/messages", "claude-fable-5-1", `,"max_completion_tokens":100`, "max_tokens is required"},
		{"native minimum tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":1`, ""},
		{"native maximum tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":128000`, ""},
		{"native zero tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":0`, "max_tokens"},
		{"native negative tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":-1`, "max_tokens"},
		{"native oversized tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":128001`, "max_tokens"},
		{"native null tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":null`, "max_tokens"},
		{"native duplicate tokens", "/v1/messages", "claude-fable-5-1", `,"max_tokens":128000,"max_tokens":1`, "duplicate max_tokens"},
		{"native noncanonical tokens", "/v1/messages", "claude-fable-5-1", `,"Max_Tokens":100`, "max_tokens"},
		{"native zero secondary token limit", "/v1/messages", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":0`, "max_completion_tokens"},
		{"native negative secondary token limit", "/v1/messages", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":-1`, "max_completion_tokens"},
		{"native oversized secondary token limit", "/v1/messages", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":128001`, "max_completion_tokens"},
		{"native completion token alias forbidden", "/v1/messages", "claude-fable-5-1", `,"max_tokens":8192,"max_completion_tokens":128`, "max_completion_tokens"},
		{"other chat zero tokens unchanged", "/v1/chat/completions", "claude-sonnet-4-5", `,"max_tokens":0`, ""},
		{"other chat oversized tokens unchanged", "/v1/chat/completions", "claude-fable-5", `,"max_tokens":128001`, ""},
		{"other chat zero completion unchanged", "/v1/chat/completions", "claude-fable-5", `,"max_completion_tokens":0`, ""},
		{"other chat both aliases unchanged", "/v1/chat/completions", "claude-sonnet-4-5", `,"max_tokens":8192,"max_completion_tokens":128`, ""},
		{"other native omission unchanged", "/v1/messages", "claude-sonnet-4-5", "", ""},
		{"other native zero unchanged", "/v1/messages", "claude-sonnet-4-5", `,"max_tokens":0`, ""},
		{"other native oversized unchanged", "/v1/messages", "claude-fable-5", `,"max_tokens":128001`, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hello"}]%s}`, tc.model, tc.params)
			c, _ := gin.CreateTestContext(httptest.NewRecorder())
			c.Request = httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(body))
			c.Request.Header.Set("Content-Type", "application/json")
			format := types.RelayFormatOpenAI
			if tc.path == "/v1/messages" {
				format = types.RelayFormatClaude
			}
			request, err := GetAndValidateRequest(c, format)
			if tc.wantErr != "" {
				require.ErrorContains(t, err, tc.wantErr)
				assert.Nil(t, request)
				return
			}
			require.NoError(t, err)
			if tc.name == "chat omitted tokens keep default" {
				chat, ok := request.(*dto.GeneralOpenAIRequest)
				require.True(t, ok)
				assert.Nil(t, chat.MaxTokens)
				assert.Nil(t, chat.MaxCompletionTokens)
			}
		})
	}
}

func TestFable51ProtocolRouteGateBeforeBilling(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		name, path, body, originalModel string
		format                          types.RelayFormat
		wantErr                         bool
	}{
		{"Responses rejected", "/v1/responses", `{"model":"claude-fable-5-1","input":"hello"}`, "", types.RelayFormatOpenAIResponses, true},
		{"Compaction rejected", "/v1/responses/compact", `{"model":"claude-fable-5-1","input":"hello"}`, "", types.RelayFormatOpenAIResponsesCompaction, true},
		{"legacy Completions rejected", "/v1/completions", `{"model":"claude-fable-5-1","prompt":"hello"}`, "", types.RelayFormatOpenAI, true},
		{"embedding rejected", "/v1/embeddings", `{"model":"claude-fable-5-1","input":"hello"}`, "", types.RelayFormatEmbedding, true},
		{"Chat path with wrong relay format rejected", "/v1/chat/completions", `{"model":"claude-fable-5-1","input":"hello"}`, "", types.RelayFormatOpenAIResponses, true},
		{"Messages other path rejected", "/v1/messages/count_tokens", `{"model":"claude-fable-5-1","max_tokens":8192,"messages":[{"role":"user","content":"hello"}]}`, "", types.RelayFormatClaude, true},
		{"routing model rejected", "/v1/responses", `{"model":"claude-sonnet-4-5","input":"hello"}`, "claude-fable-5-1", types.RelayFormatOpenAIResponses, true},
		{"duplicate first model cannot bypass", "/v1/responses", `{"model":"claude-fable-5-1","model":"claude-sonnet-4-5","input":"hello"}`, "", types.RelayFormatOpenAIResponses, true},
		{"duplicate final model cannot bypass", "/v1/responses", `{"model":"claude-sonnet-4-5","model":"claude-fable-5-1","input":"hello"}`, "claude-sonnet-4-5", types.RelayFormatOpenAIResponses, true},
		{"other Responses unchanged", "/v1/responses", `{"model":"claude-sonnet-4-5","input":"hello"}`, "", types.RelayFormatOpenAIResponses, false},
		{"other Compaction unchanged", "/v1/responses/compact", `{"model":"claude-fable-5","input":"hello"}`, "", types.RelayFormatOpenAIResponsesCompaction, false},
		{"other Completions unchanged", "/v1/completions", `{"model":"claude-sonnet-4-5","prompt":"hello"}`, "", types.RelayFormatOpenAI, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, _ := gin.CreateTestContext(httptest.NewRecorder())
			if tc.originalModel != "" {
				c.Set("original_model", tc.originalModel)
			}
			c.Request = httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(tc.body))
			c.Request.Header.Set("Content-Type", "application/json")
			request, err := GetAndValidateRequest(c, tc.format)
			if tc.wantErr {
				require.ErrorContains(t, err, "claude-fable-5-1 only supports /v1/chat/completions and /v1/messages")
				assert.Nil(t, request)
				return
			}
			require.NoError(t, err)
			assert.NotNil(t, request)
		})
	}
}
