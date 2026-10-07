package main

import (
	"errors"
	"math"
	"net"
	"net/url"
	"regexp"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
)

var imageIdentityPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

// normalizeImageInput validates only declared image-reference modes. Execution
// eligibility, actual image bytes and the reserve price are checked internally
// before a public wallet transaction can begin.
func normalizeImageInput(body map[string]interface{}) (bool, error) {
	mode, hasMode := body["mode"]
	_, hasImages := body["images"]
	_, hasRoles := body["image_roles"]
	_, hasIdentities := body["image_identities"]
	if !hasImages && !hasRoles && !hasIdentities && (!hasMode || mode == "text") {
		delete(body, "mode") // preserve the pre-existing text fingerprint exactly
		return false, nil
	}
	name, _ := body["model"].(string)
	if (name != "grok-video-3" && name != "grok-imagine-1.5-video" && name != "grok-imagine-video-official") || (mode != "reference" && mode != "all_reference") {
		return false, errors.New("unsupported image model/mode")
	}
	images, imagesOK := body["images"].([]interface{})
	roles, rolesOK := body["image_roles"].([]interface{})
	identities, identitiesOK := body["image_identities"].([]interface{})
	if !imagesOK || !rolesOK || !identitiesOK || len(images) == 0 || len(images) > 7 || len(roles) != len(images) || len(identities) != len(images) || (mode == "reference" && len(images) != 1) || (mode == "all_reference" && len(images) < 2) {
		return false, errors.New("invalid image arrays/count")
	}
	duration, durationOK := body["duration"].(float64)
	resolution, resolutionOK := body["resolution"].(string)
	if !durationOK || math.IsNaN(duration) || math.IsInf(duration, 0) || duration != math.Trunc(duration) || duration < 1 || duration > 30 || !resolutionOK || (resolution != "480p" && resolution != "720p") {
		return false, errors.New("invalid image output specification")
	}
	for index, value := range images {
		raw, ok := value.(string)
		identity, identityOK := identities[index].(string)
		if !ok || len(raw) > 4096 || roles[index] != "reference" || !identityOK || !imageIdentityPattern.MatchString(identity) {
			return false, errors.New("invalid image role or identity")
		}
		parsed, err := url.Parse(raw)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || (parsed.Port() != "" && parsed.Port() != "443") {
			return false, errors.New("invalid image source URL")
		}
		if ip := net.ParseIP(parsed.Hostname()); ip != nil && (!ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast()) {
			return false, errors.New("private image source URL")
		}
	}
	return true, nil
}

// preflightImage calls an authenticated, non-generating validation endpoint.
// It returns only an execution-verified retail reservation and never accepts a
// caller-supplied quote or an approximate model-wide fallback price.
func (s *server) preflightImage(body map[string]interface{}) (videoSpec, int, error) {
	if err := s.nativeReady(); err != nil {
		return videoSpec{}, 503, err
	}
	wire, err := common.Marshal(body)
	if err != nil {
		return videoSpec{}, 503, err
	}
	data, status, err := s.backendJSON("POST", "/v1/operations/video-input-validation", wire)
	if err != nil {
		return videoSpec{}, 503, err
	}
	if status != 200 {
		if status != 400 && status != 409 && status != 413 && status != 429 {
			status = 503
		}
		return videoSpec{}, status, errors.New("image input validation failed")
	}
	resolution := body["resolution"].(string)
	duration := body["duration"].(float64)
	if data["ok"] != true || data["billing_contract_version"] != "xtai-video-billing-v2.2" || data["task_created"] != false || data["upstream_submitted"] != false || data["model"] != body["model"] || data["mode"] != body["mode"] || data["resolution"] != resolution || data["duration"] != duration || data["image_count"] != float64(len(body["images"].([]interface{}))) {
		return videoSpec{}, 503, errors.New("image input validation contract mismatch")
	}
	amount, ok := data["reserved_cny_exact"].(string)
	revision, revisionOK := data["pricing_revision"].(string)
	price, err := decimal.NewFromString(amount)
	if !ok || !revisionOK || revision == "" || err != nil || !price.IsPositive() || price.GreaterThan(decimal.NewFromInt(100)) || price.Exponent() < -6 {
		return videoSpec{}, 503, errors.New("invalid verified image input price")
	}
	return videoSpec{Resolution: resolution, Duration: int(duration), Reserve: price.StringFixed(6)}, 200, nil
}
