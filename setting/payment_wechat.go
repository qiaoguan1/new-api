package setting

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
)

type WechatPayConfig struct {
	Enabled        bool
	MchID          string
	AppID          string
	APIv3Key       string
	CertSerialNo   string
	PrivateKeyPath string
	PublicKeyID    string
	PublicKeyPath  string
	NotifyURL      string
	enabledValue   string
}

func GetWechatPayConfig() WechatPayConfig {
	enabledValue := strings.TrimSpace(os.Getenv("WECHAT_PAY_ENABLED"))
	enabled, _ := strconv.ParseBool(enabledValue)
	return WechatPayConfig{
		Enabled:        enabled,
		MchID:          strings.TrimSpace(os.Getenv("WECHAT_PAY_MCH_ID")),
		AppID:          strings.TrimSpace(os.Getenv("WECHAT_PAY_APP_ID")),
		APIv3Key:       strings.TrimSpace(os.Getenv("WECHAT_PAY_API_V3_KEY")),
		CertSerialNo:   strings.TrimSpace(os.Getenv("WECHAT_PAY_CERT_SERIAL_NO")),
		PrivateKeyPath: strings.TrimSpace(os.Getenv("WECHAT_PAY_PRIVATE_KEY_PATH")),
		PublicKeyID:    strings.TrimSpace(os.Getenv("WECHAT_PAY_PUBLIC_KEY_ID")),
		PublicKeyPath:  strings.TrimSpace(os.Getenv("WECHAT_PAY_PUBLIC_KEY_PATH")),
		NotifyURL:      strings.TrimSpace(os.Getenv("WECHAT_PAY_NOTIFY_URL")),
		enabledValue:   enabledValue,
	}
}

func (c WechatPayConfig) Validate() error {
	if c.enabledValue == "" {
		return errors.New("WECHAT_PAY_ENABLED 未配置")
	}
	if _, err := strconv.ParseBool(c.enabledValue); err != nil {
		return errors.New("WECHAT_PAY_ENABLED 必须为布尔值")
	}
	if !c.Enabled {
		return errors.New("微信支付未启用")
	}
	missing := make([]string, 0, 6)
	if c.MchID == "" {
		missing = append(missing, "WECHAT_PAY_MCH_ID")
	}
	if c.AppID == "" {
		missing = append(missing, "WECHAT_PAY_APP_ID")
	}
	if c.APIv3Key == "" {
		missing = append(missing, "WECHAT_PAY_API_V3_KEY")
	}
	if c.CertSerialNo == "" {
		missing = append(missing, "WECHAT_PAY_CERT_SERIAL_NO")
	}
	if c.PrivateKeyPath == "" {
		missing = append(missing, "WECHAT_PAY_PRIVATE_KEY_PATH")
	}
	if c.PublicKeyID == "" {
		missing = append(missing, "WECHAT_PAY_PUBLIC_KEY_ID")
	}
	if c.PublicKeyPath == "" {
		missing = append(missing, "WECHAT_PAY_PUBLIC_KEY_PATH")
	}
	if c.NotifyURL == "" {
		missing = append(missing, "WECHAT_PAY_NOTIFY_URL")
	}
	if len(missing) > 0 {
		return fmt.Errorf("微信支付配置缺失: %s", strings.Join(missing, ", "))
	}
	if len([]byte(c.APIv3Key)) != 32 {
		return errors.New("WECHAT_PAY_API_V3_KEY 必须为 32 字节")
	}
	if len(c.MchID) < 8 || len(c.MchID) > 32 || !isASCIIDigits(c.MchID) {
		return errors.New("WECHAT_PAY_MCH_ID 格式无效")
	}
	if !strings.HasPrefix(c.AppID, "wx") || len(c.AppID) < 10 || len(c.AppID) > 32 {
		return errors.New("WECHAT_PAY_APP_ID 格式无效")
	}
	if len(c.CertSerialNo) < 16 || !isASCIIHex(c.CertSerialNo) {
		return errors.New("WECHAT_PAY_CERT_SERIAL_NO 格式无效")
	}
	if !strings.HasPrefix(c.PublicKeyID, "PUB_KEY_ID_") {
		return errors.New("WECHAT_PAY_PUBLIC_KEY_ID 格式无效")
	}
	notifyURL, err := url.ParseRequestURI(c.NotifyURL)
	if err != nil || notifyURL.Scheme != "https" || notifyURL.Host == "" || notifyURL.User != nil || notifyURL.RawQuery != "" || notifyURL.Fragment != "" {
		return errors.New("WECHAT_PAY_NOTIFY_URL 必须为不带用户信息、查询参数和片段的 HTTPS URL")
	}
	info, err := os.Stat(c.PrivateKeyPath)
	if err != nil {
		return errors.New("WECHAT_PAY_PRIVATE_KEY_PATH 无法读取")
	}
	if info.IsDir() {
		return errors.New("WECHAT_PAY_PRIVATE_KEY_PATH 不能是目录")
	}
	pubInfo, err := os.Stat(c.PublicKeyPath)
	if err != nil {
		return errors.New("WECHAT_PAY_PUBLIC_KEY_PATH 无法读取")
	}
	if pubInfo.IsDir() {
		return errors.New("WECHAT_PAY_PUBLIC_KEY_PATH 不能是目录")
	}
	return nil
}

func isASCIIDigits(value string) bool {
	for _, char := range value {
		if char < '0' || char > '9' {
			return false
		}
	}
	return value != ""
}

func isASCIIHex(value string) bool {
	for _, char := range value {
		if !((char >= '0' && char <= '9') || (char >= 'a' && char <= 'f') || (char >= 'A' && char <= 'F')) {
			return false
		}
	}
	return value != ""
}

func IsWechatPayEnabled() bool {
	return GetWechatPayConfig().Validate() == nil
}
