package middleware

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestStandaloneSearchPathDisablesConversationChannelAffinity(t *testing.T) {
	require.True(t, isStandaloneSearchPath("/v1/alpha/search"))
	require.False(t, isStandaloneSearchPath("/v1/responses"))
	require.False(t, isStandaloneSearchPath("/v1/alpha/search/other"))
}
