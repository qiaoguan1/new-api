package relay

import (
	"fmt"
	"strings"

	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
)

const (
	VideoBillingContractContextKey  = "video_billing_contract_version"
	VideoOfficialRevisionContextKey = "video_official_pricing_revision"
	VideoOfficialPricingRevision    = "official-fallback-2026-08-09.1"
	VideoBillingContractVersion     = constant.XingTuVideoContractCurrent
)

type officialVideoSKU struct {
	model                   string
	resolution              string
	officialTenMicrosPerSec int64
}

var officialVideoAliases = map[string]officialVideoSKU{
	"seedance-2.0-480p":             {"seedance-2.0", "480p", 4419450},
	"sd2-480p":                      {"seedance-2.0", "480p", 4419450},
	"seedance2.0-selfsur-720p":      {"seedance-2.0", "720p", 9936000},
	"seedance-2.0-720p":             {"seedance-2.0", "720p", 9936000},
	"sd2-720p":                      {"seedance-2.0", "720p", 9936000},
	"sd2-pro-720p":                  {"seedance-2.0", "720p", 9936000},
	"value-sd-premium-720p":         {"seedance-2.0", "720p", 9936000},
	"seedance-2.0-1080p":            {"seedance-2.0", "1080p", 24786000},
	"sd2-1080p":                     {"seedance-2.0", "1080p", 24786000},
	"seedance-2.0-fast-480p":        {"seedance-2.0-fast", "480p", 3554775},
	"sd2-fast-480p":                 {"seedance-2.0-fast", "480p", 3554775},
	"seedance2.0-selfsur-fast-720p": {"seedance-2.0-fast", "720p", 7992000},
	"seedance-2.0-fast-720p":        {"seedance-2.0-fast", "720p", 7992000},
	"sd2-fast-720p":                 {"seedance-2.0-fast", "720p", 7992000},
	"seedance-2.0-mini-480p":        {"seedance-2.0-mini", "480p", 2209725},
	"sd2-mini-480p":                 {"seedance-2.0-mini", "480p", 2209725},
	"seedance-2.0-mini-720p":        {"seedance-2.0-mini", "720p", 4968000},
	"sd2-mini-720p":                 {"seedance-2.0-mini", "720p", 4968000},
}

var officialVideoStableRates = map[string]map[string]int64{
	"seedance-2.0":      {"480p": 4419450, "720p": 9936000, "1080p": 24786000},
	"seedance-2.0-fast": {"480p": 3554775, "720p": 7992000},
	"seedance-2.0-mini": {"480p": 2209725, "720p": 4968000},
}

func normalizeVideoResolution(size string) string {
	switch strings.ToLower(strings.TrimSpace(size)) {
	case "480p", "854x480", "480x854":
		return "480p"
	case "720p", "1280x720", "720x1280":
		return "720p"
	case "1080p", "1920x1080", "1080x1920":
		return "1080p"
	default:
		return ""
	}
}

func officialVideoQuote(modelName, size string, seconds int) (quota int, sku officialVideoSKU, matched bool, err error) {
	name := strings.ToLower(strings.TrimSpace(modelName))
	rawResolution := strings.TrimSpace(size)
	requestedResolution := normalizeVideoResolution(rawResolution)
	if alias, ok := officialVideoAliases[name]; ok {
		sku, matched = alias, true
		if rawResolution != "" && requestedResolution == "" {
			return 0, sku, true, fmt.Errorf("unsupported video resolution %s", rawResolution)
		}
		if requestedResolution != "" && requestedResolution != alias.resolution {
			return 0, sku, true, fmt.Errorf("model resolution %s conflicts with requested resolution %s", alias.resolution, requestedResolution)
		}
	} else if rates, ok := officialVideoStableRates[name]; ok {
		resolution := requestedResolution
		if resolution == "" {
			return 0, sku, true, fmt.Errorf("a supported video resolution is required")
		}
		rate, ok := rates[resolution]
		if !ok {
			return 0, sku, true, fmt.Errorf("resolution %s is not supported by %s", resolution, name)
		}
		sku, matched = officialVideoSKU{name, resolution, rate}, true
	}
	if !matched {
		return 0, sku, false, nil
	}
	if seconds < 1 || seconds > 3600 {
		return 0, sku, true, fmt.Errorf("video duration must be between 1 and 3600 seconds")
	}
	// Rates retain Ark's seventh decimal. Compute the whole request first,
	// multiply by 1.5, then round up once at the final quota boundary.
	numerator := sku.officialTenMicrosPerSec * int64(seconds) * 3
	quota64 := (numerator + 39) / 40
	return int(quota64), sku, true, nil
}

// ValidateOfficialVideoRequest ensures the public v2 contract can create a
// frozen Ark-official reservation for the requested stable SKU.
func ValidateOfficialVideoRequest(modelName, resolution string, seconds int) error {
	stableName := strings.TrimSpace(modelName)
	if _, ok := officialVideoStableRates[stableName]; !ok {
		return fmt.Errorf("unsupported XingTu video model %s", modelName)
	}
	_, _, matched, err := officialVideoQuote(modelName, resolution, seconds)
	if err != nil {
		return err
	}
	if !matched {
		return fmt.Errorf("unsupported XingTu video model %s", modelName)
	}
	return nil
}

func applyOfficialVideoReservation(c *gin.Context, info *relaycommon.RelayInfo) (bool, error) {
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return false, err
	}
	seconds := req.Duration
	if seconds == 0 && req.Seconds != "" {
		for _, r := range req.Seconds {
			if r < '0' || r > '9' {
				return false, fmt.Errorf("video seconds must be an integer")
			}
			seconds = seconds*10 + int(r-'0')
		}
	}
	resolution := req.Size
	if resolution == "" {
		resolution = req.Resolution
	}
	quota, _, matched, err := officialVideoQuote(info.OriginModelName, resolution, seconds)
	if err != nil || !matched {
		return matched, err
	}
	info.PriceData.Quota = quota
	info.PriceData.FreeModel = false
	info.HardQuota = true
	c.Set(VideoBillingContractContextKey, VideoBillingContractVersion)
	c.Set(VideoOfficialRevisionContextKey, VideoOfficialPricingRevision)
	return true, nil
}
