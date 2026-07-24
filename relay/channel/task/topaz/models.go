package topaz

import (
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
)

const ChannelName = "Topaz"

// ModelList is the reviewed Topaz video upscale and enhancement family. Live discovery intersects
// this list with /video/status so temporarily unavailable or retired models are not exposed.
var ModelList = []string{
	"aaa-9",
	"aaa-10",
	"ahq-12",
	"alq-13",
	"alqs-2",
	"amq-13",
	"amqs-2",
	"ddv-3",
	"dtd-4",
	"dtds-2",
	"dtv-4",
	"dtvs-2",
	"ganim-1",
	"gcg-5",
	"ghq-5",
	"iris-2",
	"iris-3",
	"pnat-1",
	"prob-4",
	"rhea-1",
	"sl-1",
	"slc-1",
	"slf-1",
	"slf-2",
	"slhq-1",
	"slm-1",
	"slp-2",
	"slp-2.5",
	"wonder-1",
	"thd-3",
	"thf-4",
}

var upscaleModelSet = func() map[string]struct{} {
	result := make(map[string]struct{}, len(ModelList))
	for _, model := range ModelList {
		result[model] = struct{}{}
	}
	return result
}()

type systemStatusResponse struct {
	IsAvailable         bool     `json:"isAvailable"`
	AvailabilityMessage string   `json:"availabilityMessage"`
	SupportedModels     []string `json:"supportedModels"`
}

func isUpscaleModel(model string) bool {
	_, ok := upscaleModelSet[model]
	return ok
}

func filterUpscaleModels(models []string) []string {
	result := make([]string, 0, len(models))
	seen := make(map[string]struct{}, len(models))
	for _, model := range models {
		model = strings.TrimSpace(model)
		if !isUpscaleModel(model) {
			continue
		}
		if _, ok := seen[model]; ok {
			continue
		}
		seen[model] = struct{}{}
		result = append(result, model)
	}
	return result
}

// FetchUpscaleModels returns Topaz's currently available video upscaling models.
func FetchUpscaleModels(baseURL, apiKey, proxy string) ([]string, error) {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if baseURL == "" {
		return nil, fmt.Errorf("Topaz base URL is required")
	}
	if strings.TrimSpace(apiKey) == "" {
		return nil, fmt.Errorf("Topaz API key is required")
	}

	req, err := http.NewRequest(http.MethodGet, baseURL+"/video/status", nil)
	if err != nil {
		return nil, fmt.Errorf("create Topaz status request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-API-Key", apiKey)

	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("create Topaz HTTP client: %w", err)
	}
	client = topazHTTPClient(client, topazRequestTimeout)
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request Topaz status: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Topaz status returned HTTP %d", resp.StatusCode)
	}

	var status systemStatusResponse
	if err := common.DecodeJson(io.LimitReader(resp.Body, maxTopazJSONBytes), &status); err != nil {
		return nil, fmt.Errorf("decode Topaz status: %w", err)
	}
	if !status.IsAvailable {
		message := strings.TrimSpace(status.AvailabilityMessage)
		if message == "" {
			message = "system is unavailable"
		}
		return nil, fmt.Errorf("Topaz is unavailable: %s", message)
	}

	models := filterUpscaleModels(status.SupportedModels)
	if len(models) == 0 {
		return nil, fmt.Errorf("Topaz returned no supported video upscaling models")
	}
	return models, nil
}
