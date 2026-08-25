package constant

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPath2RelayModeStandaloneSearch(t *testing.T) {
	require.Equal(t, RelayModeSearch, Path2RelayMode("/v1/alpha/search"))
	require.Equal(t, RelayModeResponses, Path2RelayMode("/v1/responses"))
}
