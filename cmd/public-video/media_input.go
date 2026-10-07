package main

import (
	"errors"
	"math"
	"regexp"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
)

var mediaSecondsPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.[0-9]{6}$`)

type nodyMediaLimits struct {
	images, videos, audios int
}

// These are model-specific candidate limits, not advertised execution profiles.
// Shared Seedance reference rules remain separate; exact admission is still
// determined by authenticated media verification and a matching retail quote.
var nodyMediaCandidateLimits = map[string]nodyMediaLimits{
	"wan3.0-video": {10, 5, 5}, "wan3.0-video-prime": {10, 5, 5},
	"omni-flash": {3, 1, 0}, "flux-3-video": {10, 0, 0},
	"grok-video-3": {7, 0, 0}, "grok-imagine-1.5-video": {7, 0, 0}, "grok-imagine-video-official": {7, 0, 0},
}

// The six deployed image tuples are not the text defaults: Imagine 1.5 images
// were accepted at 720p while its original text entry remains 480p.
var legacyGrokImageSpecs = map[string]videoSpec{
	"grok-video-3":                {Resolution: "720p", Duration: 6},
	"grok-imagine-1.5-video":      {Resolution: "720p", Duration: 6},
	"grok-imagine-video-official": {Resolution: "480p", Duration: 1},
}

// normalizeNodyMediaInput accepts bounded candidate syntax, not execution
// eligibility. The authenticated gateway must verify actual bytes and an exact
// mode/count/metadata quote before a new wallet reservation can be created.
func normalizeNodyMediaInput(body map[string]interface{}) (bool, error) {
	for _, field := range []string{"reference_videos", "reference_audios"} {
		if items, ok := body[field].([]interface{}); ok && len(items) == 0 {
			delete(body, field)
		}
	}
	_, canonicalImages := body["reference_images"]
	_, videosPresent := body["reference_videos"]
	_, audiosPresent := body["reference_audios"]
	name, _ := body["model"].(string)
	limits, knownModel := nodyMediaCandidateLimits[name]
	if !knownModel {
		return false, errors.New("unknown Nody media model")
	}
	mode, _ := body["mode"].(string)
	grok := name == "grok-video-3" || name == "grok-imagine-1.5-video" || name == "grok-imagine-video-official"
	if !canonicalImages && !videosPresent && !audiosPresent && (mode == "" || mode == "text") {
		return false, nil
	}
	if canonicalImages {
		for _, field := range []string{"images", "image_roles", "image_identities"} {
			if _, exists := body[field]; exists {
				return false, errors.New("ambiguous image source")
			}
		}
		items, ok := body["reference_images"].([]interface{})
		if !ok || len(items) == 0 || len(items) > limits.images {
			return false, errors.New("invalid image reference array")
		}
		images, roles, identities := make([]interface{}, len(items)), make([]interface{}, len(items)), make([]interface{}, len(items))
		for index, value := range items {
			item, ok := value.(map[string]interface{})
			if !ok || len(item) != 3 {
				return false, errors.New("invalid image descriptor")
			}
			images[index], roles[index], identities[index] = item["url"], item["role"], item["sha256"]
		}
		body["images"], body["image_roles"], body["image_identities"] = images, roles, identities
		delete(body, "reference_images")
	}
	// Canonical aliases for the original Grok image path keep its exact frozen
	// identity and admission contract instead of creating a second billing path.
	if grok && !videosPresent && !audiosPresent && (mode == "reference" || mode == "all_reference") {
		images, _ := body["images"].([]interface{})
		legacy := legacyGrokImageSpecs[name]
		aspect, hasAspect := body["aspect_ratio"]
		audio, hasAudio := body["generate_audio"]
		if len(images) >= 1 && len(images) <= 2 && body["duration"] == float64(legacy.Duration) && body["resolution"] == legacy.Resolution && (!hasAspect || aspect == "16:9") && (!hasAudio || audio == true) {
			return false, nil
		}
	}
	if mode != "reference" && mode != "all_reference" && mode != "first_frame" && mode != "last_frame" && mode != "first_last_frame" {
		return false, errors.New("invalid media operation mode")
	}
	duration, durationOK := body["duration"].(float64)
	resolution, resolutionOK := body["resolution"].(string)
	if !durationOK || math.IsNaN(duration) || math.IsInf(duration, 0) || duration != math.Trunc(duration) || duration < 1 || duration > 30 || !resolutionOK || (resolution != "480p" && resolution != "720p" && resolution != "1080p" && resolution != "4k") {
		return false, errors.New("invalid media output specification")
	}
	if aspect, exists := body["aspect_ratio"]; exists {
		if !canonicalMediaAspectRatio(aspect) {
			return false, errors.New("invalid media aspect ratio")
		}
	} else {
		body["aspect_ratio"] = "16:9"
	}
	if audio, exists := body["generate_audio"]; exists {
		if _, ok := audio.(bool); !ok {
			return false, errors.New("invalid generated audio flag")
		}
	} else {
		body["generate_audio"] = true
	}
	images, imagesOK := body["images"].([]interface{})
	roles, rolesOK := body["image_roles"].([]interface{})
	identities, identitiesOK := body["image_identities"].([]interface{})
	if _, exists := body["images"]; exists {
		if !imagesOK || !rolesOK || !identitiesOK || len(images) == 0 || len(images) > limits.images || len(roles) != len(images) || len(identities) != len(images) {
			return false, errors.New("invalid image arrays")
		}
	} else if rolesOK || identitiesOK || body["image_roles"] != nil || body["image_identities"] != nil {
		return false, errors.New("image metadata without images")
	}
	if name == "omni-flash" && len(images) == 2 {
		return false, errors.New("Omni accepts zero, one or three images")
	}
	for index, value := range images {
		identity, identityOK := identities[index].(string)
		role := "reference"
		if mode == "first_frame" || (mode == "first_last_frame" && index == 0) {
			role = "first"
		} else if mode == "last_frame" || (mode == "first_last_frame" && index == 1) {
			role = "last"
		}
		if !identityOK || !imageIdentityPattern.MatchString(identity) || roles[index] != role || validateMediaURL(value) != nil {
			return false, errors.New("invalid image identity/role/source")
		}
	}
	for _, kind := range []string{"reference_videos", "reference_audios"} {
		value, exists := body[kind]
		if !exists {
			continue
		}
		items, ok := value.([]interface{})
		maximum := limits.videos
		if kind == "reference_audios" {
			maximum = limits.audios
		}
		if !ok || len(items) > maximum {
			return false, errors.New("invalid AV reference array")
		}
		for _, value := range items {
			if err := validateReferenceDescriptor(value, kind); err != nil {
				return false, err
			}
		}
		if len(items) == 0 {
			delete(body, kind)
			continue
		}
		if total := referenceSeconds(items); total.GreaterThan(decimal.NewFromInt(int64(maximum * 15))) {
			return false, errors.New("reference duration total exceeds safety limit")
		}
	}
	videos, _ := body["reference_videos"].([]interface{})
	audios, _ := body["reference_audios"].([]interface{})
	if len(images)+len(videos)+len(audios) == 0 {
		return false, errors.New("invalid media input combination")
	}
	if mode == "first_frame" || mode == "last_frame" || mode == "first_last_frame" {
		want := 1
		if mode == "first_last_frame" {
			want = 2
		}
		if len(images) != want || len(videos)+len(audios) != 0 {
			return false, errors.New("frame and reference modes are exclusive")
		}
	}
	return true, nil
}

func canonicalMediaAspectRatio(value interface{}) bool {
	aspect, ok := value.(string)
	return ok && (aspect == "16:9" || aspect == "9:16" || aspect == "1:1" || aspect == "4:3" || aspect == "3:4" || aspect == "3:2" || aspect == "2:3" || aspect == "21:9" || aspect == "2:1" || aspect == "adaptive")
}

// validateReferenceDescriptor checks only canonical public metadata. Actual
// socket targets, decoder metadata and SHA-256 bytes are verified by the gateway.
func validateReferenceDescriptor(value interface{}, kind string) error {
	item, ok := value.(map[string]interface{})
	if !ok || validateMediaURL(item["url"]) != nil {
		return errors.New("invalid reference descriptor/source")
	}
	identity, ok := item["sha256"].(string)
	seconds, secondsOK := item["duration_seconds"].(string)
	if !ok || !imageIdentityPattern.MatchString(identity) || !secondsOK || len(seconds) > 9 || !mediaSecondsPattern.MatchString(seconds) {
		return errors.New("invalid reference identity/duration")
	}
	duration, err := decimal.NewFromString(seconds)
	if err != nil || duration.LessThan(decimal.NewFromInt(1)) || duration.GreaterThan(decimal.NewFromInt(15)) {
		return errors.New("invalid reference duration")
	}
	maximum := float64(200 * 1024 * 1024)
	fields := map[string]bool{"url": true, "role": true, "sha256": true, "size_bytes": true, "duration_seconds": true, "mime_type": true}
	if kind == "reference_videos" {
		fields["width_pixels"], fields["height_pixels"] = true, true
		if item["role"] != "reference_video" || item["mime_type"] != "video/mp4" || !boundedMediaInteger(item["width_pixels"], 8192) || !boundedMediaInteger(item["height_pixels"], 8192) {
			return errors.New("invalid video metadata")
		}
	} else {
		maximum = 15 * 1024 * 1024
		fields["codec"], fields["sample_rate_hz"], fields["channels"] = true, true, true
		mime, mimeOK := item["mime_type"].(string)
		codec, codecOK := item["codec"].(string)
		validFormat := (codec == "mp3" && mime == "audio/mpeg") || (codec == "wav" && (mime == "audio/wav" || mime == "audio/x-wav")) || (codec == "aac" && mime == "audio/aac") || (codec == "m4a" && (mime == "audio/mp4" || mime == "audio/x-m4a"))
		if item["role"] != "reference_audio" || !mimeOK || !codecOK || !validFormat || !boundedMediaInteger(item["sample_rate_hz"], 192000) || !boundedMediaInteger(item["channels"], 8) {
			return errors.New("invalid audio metadata")
		}
	}
	if !boundedMediaInteger(item["size_bytes"], maximum) {
		return errors.New("invalid reference size")
	}
	for field := range item {
		if !fields[field] {
			return errors.New("unknown reference descriptor field")
		}
	}
	return nil
}

func boundedMediaInteger(value interface{}, maximum float64) bool {
	number, ok := value.(float64)
	return ok && !math.IsNaN(number) && !math.IsInf(number, 0) && number > 0 && number == math.Trunc(number) && number <= maximum
}

// referenceSeconds operates only on descriptors validated by the public boundary.
func referenceSeconds(items []interface{}) decimal.Decimal {
	total := decimal.Zero
	for _, value := range items {
		item := value.(map[string]interface{})
		seconds, _ := decimal.NewFromString(item["duration_seconds"].(string))
		total = total.Add(seconds)
	}
	return total
}

// mediaFingerprintBody deliberately excludes delivery URLs while retaining
// ordered content, role and exact AV metadata identities. Legacy text and Grok
// image fingerprints are handled by the original path, unchanged.
func mediaFingerprintBody(body map[string]interface{}) map[string]interface{} {
	result := make(map[string]interface{}, len(body))
	for field, value := range body {
		result[field] = value
	}
	if images, ok := body["images"].([]interface{}); ok {
		roles := body["image_roles"].([]interface{})
		identities := body["image_identities"].([]interface{})
		references := make([]interface{}, len(images))
		for index := range images {
			references[index] = map[string]interface{}{"role": roles[index], "identity": identities[index]}
		}
		result["images"] = references
		delete(result, "image_roles")
		delete(result, "image_identities")
	}
	for _, kind := range []string{"reference_videos", "reference_audios"} {
		items, _ := body[kind].([]interface{})
		if len(items) == 0 {
			continue
		}
		references := make([]interface{}, len(items))
		for index, value := range items {
			item := value.(map[string]interface{})
			identity := make(map[string]interface{}, len(item)-1)
			for field, value := range item {
				if field != "url" {
					identity[field] = value
				}
			}
			references[index] = identity
		}
		result[kind] = references
	}
	return result
}

// preflightMedia binds every execution/pricing discriminator to the server's
// verified result. A missing field fails closed before any wallet transaction.
func (s *server) preflightMedia(body map[string]interface{}) (videoSpec, int, error) {
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
		return videoSpec{}, status, errors.New("media input validation failed")
	}
	if data["ok"] != true || data["billing_contract_version"] != "xtai-video-billing-v2.2" || data["task_created"] != false || data["upstream_submitted"] != false || data["currency"] != "CNY" || data["operation_mode"] != body["mode"] {
		return videoSpec{}, 503, errors.New("media validation contract mismatch")
	}
	for _, field := range []string{"model", "mode", "resolution", "duration", "generate_audio", "aspect_ratio"} {
		if data[field] != body[field] {
			return videoSpec{}, 503, errors.New("media quote tuple mismatch")
		}
	}
	for _, kind := range []string{"images", "reference_videos", "reference_audios"} {
		items, _ := body[kind].([]interface{})
		countField, secondsField := "image_count", ""
		if kind == "reference_videos" {
			countField, secondsField = "video_count", "input_video_seconds_exact"
		} else if kind == "reference_audios" {
			countField, secondsField = "audio_count", "input_audio_seconds_exact"
		}
		if data[countField] != float64(len(items)) {
			return videoSpec{}, 503, errors.New("media quote count mismatch")
		}
		if secondsField != "" {
			if len(items) > 0 && data[secondsField] != referenceSeconds(items).StringFixed(6) {
				return videoSpec{}, 503, errors.New("media quote measured duration mismatch")
			}
			if len(items) == 0 && data[secondsField] != nil {
				return videoSpec{}, 503, errors.New("unexpected media duration quote")
			}
		}
	}
	amount, amountOK := data["reserved_cny_exact"].(string)
	revision, revisionOK := data["pricing_revision"].(string)
	if !amountOK || len(amount) > 10 || !mediaSecondsPattern.MatchString(amount) || !revisionOK || strings.TrimSpace(revision) == "" || len(revision) > 120 {
		return videoSpec{}, 503, errors.New("invalid verified media price")
	}
	price, err := decimal.NewFromString(amount)
	if err != nil || !price.IsPositive() || price.GreaterThan(decimal.NewFromInt(150)) {
		return videoSpec{}, 503, errors.New("invalid verified media price")
	}
	return videoSpec{Resolution: body["resolution"].(string), Duration: int(body["duration"].(float64)), Reserve: price.StringFixed(6)}, 200, nil
}
