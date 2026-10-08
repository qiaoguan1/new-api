package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/shopspring/decimal"
)

const operatorEstimateSource = "nodyhub_operator_testing_estimate"
const publicReservationSchema = "xtai-public-video-estimated-reservation-v1"
const operatorEstimateWarning = "本次预扣为未验收估算，不是价格上限；最终按实际上游账单×1.5结算，实际费用可能更高。"

// validateOperatorEstimate never upgrades an estimate to verified pricing. A
// partially labeled response also fails closed instead of taking the exact path.
func validateOperatorEstimate(data map[string]interface{}) (bool, error) {
	estimated := data["price_source"] == operatorEstimateSource
	for _, field := range []string{"pricing_kind", "verification_status", "admission_mode", "is_upper_bound", "policy_digest", "reserve_cap_applied"} {
		if _, exists := data[field]; exists {
			estimated = true
		}
	}
	if !estimated {
		return false, nil
	}
	digest, digestOK := data["policy_digest"].(string)
	capApplied, capOK := data["reserve_cap_applied"].(bool)
	if data["price_source"] != operatorEstimateSource || data["pricing_kind"] != "estimated_reservation" || data["verification_status"] != "unverified" || data["admission_mode"] != "operator_testing" || data["is_upper_bound"] != false || !digestOK || !imageIdentityPattern.MatchString(digest) || !capOK || (capApplied && data["reserved_cny_exact"] != "150.000000") {
		return true, errors.New("invalid operator estimate provenance")
	}
	return true, nil
}

// freezeOperatorReservation persists the authenticated preflight tuple without
// adding database columns. The integrity digest binds its provenance to the
// URL-free request fingerprint and reserve; it is not an upstream billing proof.
func freezeOperatorReservation(body, quote map[string]interface{}) error {
	metadata := map[string]interface{}{"schema_version": publicReservationSchema}
	metadata["request_id"] = body["request_id"]
	for _, field := range []string{"ok", "billing_contract_version", "task_created", "upstream_submitted", "model", "mode", "operation_mode", "resolution", "duration", "image_count", "video_count", "audio_count", "generate_audio", "aspect_ratio", "input_video_seconds_exact", "input_audio_seconds_exact", "currency", "reserved_cny_exact", "pricing_revision", "price_source", "pricing_kind", "verification_status", "admission_mode", "is_upper_bound", "policy_digest", "reserve_cap_applied"} {
		if value, exists := quote[field]; exists {
			metadata[field] = value
		}
	}
	identity := make(map[string]interface{}, len(body))
	for field, value := range body {
		if field != "request_id" {
			identity[field] = value
		}
	}
	canonical, err := common.Marshal(mediaFingerprintBody(identity))
	if err != nil {
		return err
	}
	fingerprint := sha256.Sum256(canonical)
	metadata["payload_fingerprint"] = hex.EncodeToString(fingerprint[:])
	canonical, err = common.Marshal(metadata)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(canonical)
	metadata["envelope_digest"] = hex.EncodeToString(digest[:])
	body["_public_reservation"] = metadata
	return nil
}

type frozenPublicRequest struct {
	payload, reservation map[string]interface{}
	estimated            bool
}

// frozenPublicVideoRequest checks only durable local records, never a current
// quote/policy/provider. It removes the internal envelope before gateway calls.
func frozenPublicVideoRequest(row model.PublicVideoTask) (frozenPublicRequest, error) {
	var result frozenPublicRequest
	if common.UnmarshalJsonStr(row.Body, &result.payload) != nil || result.payload == nil {
		return result, errors.New("stored_request_invalid")
	}
	value, exists := result.payload["_public_reservation"]
	if !exists {
		return result, nil
	}
	result.estimated = true
	metadata, ok := value.(map[string]interface{})
	delete(result.payload, "_public_reservation")
	if !ok || metadata["schema_version"] != publicReservationSchema {
		return result, errors.New("stored_reservation_invalid")
	}
	result.reservation = metadata
	ownerID := sha256.Sum256([]byte(fmt.Sprintf("%d:%s", row.UserID, row.ClientRequestID)))
	requestID := "public-" + hex.EncodeToString(ownerID[:])
	if result.payload["request_id"] != requestID || metadata["request_id"] != requestID || row.ID != "vjob_"+hex.EncodeToString(ownerID[:16]) {
		return result, errors.New("stored_reservation_invalid")
	}
	storedDigest, ok := metadata["envelope_digest"].(string)
	if !ok || !imageIdentityPattern.MatchString(storedDigest) {
		return result, errors.New("stored_reservation_invalid")
	}
	sealed := make(map[string]interface{}, len(metadata)-1)
	for field, value := range metadata {
		if field != "envelope_digest" {
			sealed[field] = value
		}
	}
	canonical, err := common.Marshal(sealed)
	if err != nil {
		return result, err
	}
	digest := sha256.Sum256(canonical)
	if hex.EncodeToString(digest[:]) != storedDigest {
		return result, errors.New("stored_reservation_invalid")
	}
	// Revalidate bounded syntax before any helper can use descriptor assertions.
	mode, modePresent := result.payload["mode"]
	media, validationErr := normalizeNodyMediaInput(result.payload)
	if validationErr == nil && !media {
		_, validationErr = normalizeImageInput(result.payload)
		if modePresent {
			result.payload["mode"] = mode
		}
	}
	if validationErr != nil {
		return result, errors.New("stored_reservation_invalid")
	}
	if _, err = validateMediaPreflight(result.payload, metadata); err != nil || metadata["reserved_cny_exact"] != row.ReservedCNY || metadata["model"] != row.Model {
		return result, errors.New("stored_reservation_invalid")
	}
	identity := make(map[string]interface{}, len(result.payload))
	for field, value := range result.payload {
		if field != "request_id" {
			identity[field] = value
		}
	}
	canonical, err = common.Marshal(mediaFingerprintBody(identity))
	if err != nil {
		return result, err
	}
	fingerprint := sha256.Sum256(canonical)
	if hex.EncodeToString(fingerprint[:]) != row.Fingerprint || metadata["payload_fingerprint"] != row.Fingerprint {
		return result, errors.New("stored_reservation_invalid")
	}
	return result, nil
}

// publicOperatorRule projects a bounded candidate graph, not successful exact
// profiles. Its whitelist deliberately excludes costs, bill IDs and asset data.
func publicOperatorRule(name string, value interface{}) (map[string]interface{}, bool) {
	row, ok := value.(map[string]interface{})
	limits, known := nodyMediaCandidateLimits[name]
	if !ok || !known || row["model"] != name || row["currency"] != "CNY" || row["price_source"] != operatorEstimateSource || row["pricing_kind"] != "estimated_reservation" || row["verification_status"] != "unverified" || row["admission_mode"] != "operator_testing" || row["is_upper_bound"] != false || row["max_reserve_cny_exact"] != "150.000000" {
		return nil, false
	}
	revision, revisionOK := row["pricing_revision"].(string)
	unit, unitOK := row["estimated_cny_per_output_second_exact"].(string)
	if !revisionOK || strings.TrimSpace(revision) == "" || len(revision) > 120 || !unitOK || len(unit) > 10 || !mediaSecondsPattern.MatchString(unit) {
		return nil, false
	}
	rate, err := decimal.NewFromString(unit)
	if err != nil || !rate.IsPositive() || rate.GreaterThan(decimal.NewFromInt(150)) {
		return nil, false
	}
	public := map[string]interface{}{}
	for _, field := range []string{"model", "currency", "price_source", "pricing_kind", "verification_status", "admission_mode", "is_upper_bound", "max_reserve_cny_exact", "pricing_revision", "estimated_cny_per_output_second_exact"} {
		public[field] = row[field]
	}
	for field, allowed := range map[string]map[string]bool{
		"operation_modes": {"text": true, "reference": true, "all_reference": true, "first_frame": true, "last_frame": true, "first_last_frame": true},
		"resolutions":     {"480p": true, "720p": true, "1080p": true, "4k": true},
		"aspect_ratios":   {"16:9": true, "9:16": true, "1:1": true, "4:3": true, "3:4": true, "3:2": true, "2:3": true, "21:9": true, "2:1": true, "adaptive": true},
	} {
		values, ok := operatorStringValues(row[field], allowed)
		if !ok {
			return nil, false
		}
		public[field] = values
	}
	durations, ok := row["durations"].([]interface{})
	if !ok || len(durations) == 0 || len(durations) > 30 {
		return nil, false
	}
	minimum, maximum := float64(31), float64(0)
	seenDurations := map[float64]bool{}
	for _, value := range durations {
		duration, ok := value.(float64)
		if !ok || !boundedMediaInteger(duration, 30) || seenDurations[duration] {
			return nil, false
		}
		seenDurations[duration] = true
		minimum, maximum = math.Min(minimum, duration), math.Max(maximum, duration)
	}
	if row["duration_min"] != minimum || row["duration_max"] != maximum {
		return nil, false
	}
	public["durations"], public["duration_min"], public["duration_max"] = durations, minimum, maximum
	for field, maximum := range map[string]int{"max_images": limits.images, "max_videos": limits.videos, "max_audios": limits.audios, "max_total_assets": limits.images + limits.videos + limits.audios} {
		number, ok := row[field].(float64)
		if !ok || math.IsNaN(number) || math.IsInf(number, 0) || number < 0 || number > float64(maximum) || number != math.Trunc(number) {
			return nil, false
		}
		public[field] = number
	}
	for field, maximum := range map[string]int{"input_video": limits.videos, "input_audio": limits.audios} {
		metadata, ok := row[field].(map[string]interface{})
		if !ok {
			return nil, false
		}
		count, countOK := metadata["max_count"].(float64)
		if !countOK || count < 0 || count != math.Trunc(count) || count > float64(maximum) || metadata["min_duration_seconds"] != float64(1) || metadata["max_duration_seconds"] != float64(15) || metadata["max_total_duration_seconds"] != count*15 {
			return nil, false
		}
		public[field] = map[string]interface{}{"max_count": count, "min_duration_seconds": float64(1), "max_duration_seconds": float64(15), "max_total_duration_seconds": count * 15}
	}
	audioValues, ok := row["generate_audio_values"].([]interface{})
	if !ok || len(audioValues) == 0 || len(audioValues) > 2 {
		return nil, false
	}
	seenAudio := map[bool]bool{}
	for _, value := range audioValues {
		flag, ok := value.(bool)
		if !ok || seenAudio[flag] || (!flag && name != "wan3.0-video" && name != "wan3.0-video-prime" && name != "flux-3-video") {
			return nil, false
		}
		seenAudio[flag] = true
	}
	public["generate_audio_values"] = audioValues
	if name == "omni-flash" {
		counts, ok := row["image_input_counts"].([]interface{})
		if !ok || len(counts) != 3 || counts[0] != float64(0) || counts[1] != float64(1) || counts[2] != float64(3) {
			return nil, false
		}
		// Video editing has its own output-duration range. Keep the separate
		// text/image duration list intact; absent or mismatched bounds fail closed.
		if row["video_input_output_duration_min"] != float64(4) || row["video_input_output_duration_max"] != float64(30) {
			return nil, false
		}
		public["image_input_counts"] = counts
		public["video_input_output_duration_min"], public["video_input_output_duration_max"] = float64(4), float64(30)
	}
	return public, true
}

func operatorStringValues(value interface{}, allowed map[string]bool) ([]interface{}, bool) {
	values, ok := value.([]interface{})
	if !ok || len(values) == 0 || len(values) > len(allowed) {
		return nil, false
	}
	seen := map[string]bool{}
	for _, value := range values {
		name, ok := value.(string)
		if !ok || !allowed[name] || seen[name] {
			return nil, false
		}
		seen[name] = true
	}
	return values, true
}

// verifiedOperatorRules requires identical sanitized rules in both authenticated
// catalog and price responses. No bill/task evidence or fake profile is created.
func verifiedOperatorRules(name string, candidates, prices []interface{}) []interface{} {
	result := []interface{}{}
	if len(candidates) > 7 || len(prices) > 7 {
		return result
	}
	for _, candidate := range candidates {
		public, ok := publicOperatorRule(name, candidate)
		if !ok {
			continue
		}
		canonical, err := common.Marshal(public)
		if err != nil {
			continue
		}
		for _, price := range prices {
			other, ok := publicOperatorRule(name, price)
			if !ok {
				continue
			}
			otherCanonical, err := common.Marshal(other)
			if err == nil && string(otherCanonical) == string(canonical) {
				result = append(result, public)
				break
			}
		}
	}
	return result
}

// operatorReferenceMetadata exposes legacy-readable AV candidate flags with an
// explicit unverified admission source. It never invents successful profiles;
// any existing exact specifications are preserved separately by the caller.
func operatorReferenceMetadata(kind string, metadata map[string]interface{}, rules []interface{}) map[string]interface{} {
	matched := []interface{}{}
	resolutions := []interface{}{}
	seen := map[string]bool{}
	maxVideos, maxAudios, maxImages, maxAssets := float64(0), float64(0), float64(0), float64(0)
	generated := false
	for _, value := range rules {
		rule := value.(map[string]interface{})
		referenceMode := false
		for _, mode := range rule["operation_modes"].([]interface{}) {
			referenceMode = referenceMode || mode == "reference" || mode == "all_reference"
		}
		videos, audios, images := rule["max_videos"].(float64), rule["max_audios"].(float64), rule["max_images"].(float64)
		if !referenceMode || (kind == "reference_video" && videos == 0) || (kind == "reference_audio" && audios == 0) || (kind == "reference_video_audio" && (videos == 0 || audios == 0)) {
			continue
		}
		matched = append(matched, rule)
		for _, value := range rule["resolutions"].([]interface{}) {
			resolution := value.(string)
			if !seen[resolution] {
				resolutions = append(resolutions, resolution)
				seen[resolution] = true
			}
		}
		maxVideos, maxAudios, maxImages, maxAssets = math.Max(maxVideos, videos), math.Max(maxAudios, audios), math.Max(maxImages, images), math.Max(maxAssets, rule["max_total_assets"].(float64))
		for _, flag := range rule["generate_audio_values"].([]interface{}) {
			generated = generated || flag == true
		}
	}
	if len(matched) == 0 {
		return nil
	}
	count := maxVideos
	if kind == "reference_audio" {
		count = maxAudios
	}
	public := map[string]interface{}{"supported": true, "available": true, "identity_required": true, "available_resolutions": resolutions, "max_count": count, "max_total_assets": maxAssets, "specifications": []interface{}{}, "operator_rules": matched, "verification_status": "unverified", "admission_mode": "operator_testing", "pricing_kind": "estimated_reservation", "price_source": operatorEstimateSource, "is_upper_bound": false, "warning": operatorEstimateWarning}
	for field, allowed := range map[string]map[string]bool{
		"roles":        {"reference_video": true, "reference_audio": true},
		"mime_types":   {"video/mp4": true, "audio/mpeg": true, "audio/wav": true, "audio/x-wav": true, "audio/aac": true, "audio/mp4": true, "audio/x-m4a": true},
		"video_codecs": {"h264": true, "hevc": true}, "audio_codecs": {"mp3": true, "wav": true, "aac": true, "m4a": true},
	} {
		if values, ok := operatorStringValues(metadata[field], allowed); ok {
			public[field] = values
		}
	}
	public["min_duration_seconds"], public["max_duration_seconds"] = float64(1), float64(15)
	switch kind {
	case "reference_video":
		public["roles"] = []string{"reference_video"}
		public["max_video_bytes"] = float64(200 * 1024 * 1024)
		public["supports_images_with_video"], public["supports_audio_with_video"], public["supports_generate_audio_with_video"] = maxImages > 0, maxAudios > 0, generated
	case "reference_audio":
		public["roles"] = []string{"reference_audio"}
		public["max_audio_bytes"] = float64(15 * 1024 * 1024)
		public["requires_non_audio_input"] = false // only Wan candidate rules permit audio, including solo audio
		public["supports_images_with_audio"], public["supports_video_with_audio"], public["supports_generate_audio_with_reference_audio"] = maxImages > 0, maxVideos > 0, generated
	case "reference_video_audio":
		public["roles"] = []string{"reference_video", "reference_audio"}
		public["max_video_count"], public["max_audio_count"] = maxVideos, maxAudios
		public["supports_images_with_video_audio"], public["supports_generate_audio_with_video_audio"] = maxImages > 0, generated
	}
	return public
}
