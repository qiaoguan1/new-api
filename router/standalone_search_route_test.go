package router

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestRelayRouterRegistersStandaloneSearch(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	SetRelayRouter(engine)

	found := false
	for _, route := range engine.Routes() {
		if route.Method == "POST" && route.Path == "/v1/alpha/search" {
			found = true
			break
		}
	}
	require.True(t, found, "standalone search route must be registered")
}

func TestStandaloneSearchRequiresBearerAuthentication(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	SetRelayRouter(engine)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/v1/alpha/search", bytes.NewBufferString(`{"id":"search_1","model":"gpt-5.6-sol"}`))
	request.Header.Set("Content-Type", "application/json")

	engine.ServeHTTP(recorder, request)
	require.Equal(t, http.StatusUnauthorized, recorder.Code)
}
