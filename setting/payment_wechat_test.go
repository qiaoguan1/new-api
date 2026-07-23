package setting

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGetWechatPayConfigReadsOnlyRequiredEnvironmentVariables(t *testing.T) {
	keyPath := filepath.Join(t.TempDir(), "apiclient_key.pem")
	require.NoError(t, writeTestFile(keyPath))
	publicKeyPath := filepath.Join(t.TempDir(), "wechatpay_public_key.pem")
	require.NoError(t, writeTestFile(publicKeyPath))

	t.Setenv("WECHAT_PAY_ENABLED", "true")
	t.Setenv("WECHAT_PAY_MCH_ID", "1900000109")
	t.Setenv("WECHAT_PAY_APP_ID", "wx1234567890abcd")
	t.Setenv("WECHAT_PAY_API_V3_KEY", "12345678901234567890123456789012")
	t.Setenv("WECHAT_PAY_CERT_SERIAL_NO", "0123456789ABCDEF")
	t.Setenv("WECHAT_PAY_PRIVATE_KEY_PATH", keyPath)
	t.Setenv("WECHAT_PAY_PUBLIC_KEY_ID", "PUB_KEY_ID_0123456789ABCDEF")
	t.Setenv("WECHAT_PAY_PUBLIC_KEY_PATH", publicKeyPath)
	t.Setenv("WECHAT_PAY_NOTIFY_URL", "https://example.com/api/wechatpay/notify")

	cfg := GetWechatPayConfig()
	require.Equal(t, "1900000109", cfg.MchID)
	require.NoError(t, cfg.Validate())
}

func TestWechatPayConfigValidateRejectsInvalidValues(t *testing.T) {
	t.Run("missing enabled flag", func(t *testing.T) {
		cfg := WechatPayConfig{}
		require.ErrorContains(t, cfg.Validate(), "WECHAT_PAY_ENABLED")
	})

	t.Run("invalid enabled flag", func(t *testing.T) {
		cfg := WechatPayConfig{Enabled: false, enabledValue: "yes"}
		require.ErrorContains(t, cfg.Validate(), "WECHAT_PAY_ENABLED")
	})

	t.Run("invalid API v3 key length", func(t *testing.T) {
		cfg := validWechatPayConfig(t)
		cfg.APIv3Key = "short"
		require.ErrorContains(t, cfg.Validate(), "WECHAT_PAY_API_V3_KEY")
	})

	t.Run("non HTTPS notify URL", func(t *testing.T) {
		cfg := validWechatPayConfig(t)
		cfg.NotifyURL = "http://example.com/api/wechatpay/notify"
		require.ErrorContains(t, cfg.Validate(), "WECHAT_PAY_NOTIFY_URL")
	})
}

func validWechatPayConfig(t *testing.T) WechatPayConfig {
	t.Helper()
	keyPath := filepath.Join(t.TempDir(), "apiclient_key.pem")
	require.NoError(t, writeTestFile(keyPath))
	publicKeyPath := filepath.Join(t.TempDir(), "wechatpay_public_key.pem")
	require.NoError(t, writeTestFile(publicKeyPath))
	return WechatPayConfig{
		Enabled:        true,
		enabledValue:   "true",
		MchID:          "1900000109",
		AppID:          "wx1234567890abcd",
		APIv3Key:       "12345678901234567890123456789012",
		CertSerialNo:   "0123456789ABCDEF",
		PrivateKeyPath: keyPath,
		PublicKeyID:    "PUB_KEY_ID_0123456789ABCDEF",
		PublicKeyPath:  publicKeyPath,
		NotifyURL:      "https://example.com/api/wechatpay/notify",
	}
}

func writeTestFile(path string) error {
	return os.WriteFile(path, []byte("test"), 0o600)
}
