"""Fail-closed minimal backport onto the verified issue180 production source."""
import hashlib
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
SOURCE = ROOT / 'source'


def replace(path, old, new, count=1, limit=-1):
    destination = SOURCE / path
    text = destination.read_text()
    assert text.count(old) == count, 'unexpected source anchor: ' + path
    destination.write_text(text.replace(old, new, limit))


def main():
    marker = ROOT / 'source-patched.sha256'
    assert not marker.exists(), 'source already patched; do not repeat substitutions'
    expected = '5d789508af769fdfdb986a323ec44837db77c32369af649a1a0b05bc77fa03b2'
    assert hashlib.sha256((SOURCE / 'model/quota_authority.go').read_bytes()).hexdigest() == expected
    replace('dto/channel_settings.go', '\tSystemPromptOverride   bool   `json:"system_prompt_override,omitempty"`', '\tSystemPromptOverride   bool   `json:"system_prompt_override,omitempty"`\n\tImageEndpoints []string `json:"image_endpoints,omitempty"`\n\tImageSizes []string `json:"image_sizes,omitempty"`')
    replace('dto/openai_response.go', '\tCachedCreationTokens int `json:"cached_creation_tokens,omitempty"`', '\tCachedCreationTokens int `json:"cached_creation_tokens,omitempty"`\n\tCacheWriteTokens int `json:"cache_write_tokens,omitempty"`')
    replace('dto/openai_response.go', 'type OutputTokenDetails struct {', '''func (d InputTokenDetails) CacheCreationTokensTotal() int {
    total := d.CachedCreationTokens
    if d.CacheWriteTokens > total { total = d.CacheWriteTokens }
    if total < 0 { return 0 }
    return total
}

type OutputTokenDetails struct {''')
    replace('service/tiered_settle.go', 'float64(usage.PromptTokensDetails.CachedCreationTokens)', 'float64(usage.PromptTokensDetails.CacheCreationTokensTotal())')
    for prefix in ['responsesResponse', 'streamResponse.Response']:
        old = 'usage.PromptTokensDetails.CachedTokens = ' + prefix + '.Usage.InputTokensDetails.CachedTokens'
        new = old + '\nusage.PromptTokensDetails.CachedCreationTokens = ' + prefix + '.Usage.InputTokensDetails.CachedCreationTokens\nusage.PromptTokensDetails.CacheWriteTokens = ' + prefix + '.Usage.InputTokensDetails.CacheWriteTokens'
        replace('relay/channel/openai/relay_responses.go', old, new)
    replace('controller/relay.go', '\tfor {\n\t\trelayInfo.RetryIndex = retryParam.GetRetry()', '\tfor attempt := 0; attempt <= common.RetryTimes; attempt++ {\n\t\tretryParam.SetRetry(attempt)\n\t\trelayInfo.RetryIndex = attempt')
    replace('controller/relay.go', '\t\t\tnewAPIError = channelErr', '\t\t\tif relayInfo.LastError != nil { newAPIError = relayInfo.LastError } else { newAPIError = channelErr }')
    replace('controller/relay.go', '????????????? %s ?????', 'no available image/model route for %s')
    replace('controller/relay.go', '???? %s ??? %s ????????retry?: %s', 'available channel selection failed for group %s model %s: %s')
    replace('controller/relay.go', '?? %s ??? %s ?????????retry?', 'no available channel for group %s model %s')
    replace('controller/relay.go', '\t\trelayInfo.LastError = newAPIError', '\t\tnewAPIError = service.NormalizeImageSubmissionError(c, newAPIError)\n\t\trelayInfo.LastError = newAPIError')
    replace('controller/relay.go', '\t\texcludedChannels[channel.Id] = true', '\t\tservice.RecordImageRouteRejection(c, channel.Id, relayInfo.OriginModelName, newAPIError)\n\t\texcludedChannels[channel.Id] = true\n\t\tif !shouldRetry(c, newAPIError, common.RetryTimes-attempt) { break }')
    # First occurrence is the synchronous Relay loop; leave task/video Relay alone.
    replace('controller/relay.go', '\t\taddUsedChannel(c, channel.Id)', '\t\taddUsedChannel(c, channel.Id)\n\t\tif service.IsImageRequestPath(c.Request.URL.Path) { c.Set("xtai_image_submit_started", false); c.Header(common.ImageSubmissionStateHeader, "not_submitted") }', count=2, limit=1)
    replace('controller/relay.go', '''func shouldRetry(c *gin.Context, openaiErr *types.NewAPIError, retryTimes int) bool {
	if openaiErr == nil {
		return false
	}''', '''func shouldRetry(c *gin.Context, openaiErr *types.NewAPIError, retryTimes int) bool {
	if openaiErr == nil { return false }
    if c.Request != nil && service.IsImageRequestPath(c.Request.URL.Path) {
        _, specific := c.Get("specific_channel_id")
        return retryTimes > 0 && !specific && !types.IsSkipRetryError(openaiErr) && !service.ShouldSkipRetryAfterChannelAffinityFailure(c) && service.IsSafeImageRejection(openaiErr)
    }''')
    for group in ['autoGroup', 'param.TokenGroup']:
        old = 'model.GetRandomSatisfiedChannel(' + group + ', param.ModelName, excluded)'
        new = 'SelectImageRoute(param.Ctx, param.ModelName, excluded, func(excluded map[int]bool) (*model.Channel, error) { return ' + old + ' })'
        replace('service/channel_select.go', old, new)
    replace('relay/channel/api_request.go', '\tresp, err := client.Do(req)', '\treq, client = imageRequestClient(c, req, client)\n\tresp, err := client.Do(req)')
    # The middleware has the same small source in both revisions; copy the
    # reviewed implementation rather than duplicating its audit validation.
    # Old production copies every upstream request-ID header; preserve the new
    # immutable relay ID even when an upstream sends a conflicting value.
    replace('service/http.go', 'if k == "Content-Length" {', 'if k == "Content-Length" || strings.EqualFold(k, common.ImageRelayRequestIDHeader) || strings.EqualFold(k, common.ImageSubmissionStateHeader) {')
    replace('service/http.go', '\t"net/http"', '\t"net/http"\n\t"strings"')
    replace('model/log.go', 'otherStr := common.MapToJsonStr(other)', 'otherStr := common.MapToJsonStr(appendImageClientAudit(c, other))')
    replace('model/log.go', 'otherStr := common.MapToJsonStr(params.Other)', 'otherStr := common.MapToJsonStr(appendImageClientAudit(c, params.Other))')
    copied = []
    for path in (ROOT / 'native-overlay').rglob('*.go'):
        relative = path.relative_to(ROOT / 'native-overlay')
        destination = SOURCE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied.append(relative.as_posix())
    shutil.copy2(ROOT / 'native_request_id.go', SOURCE / 'middleware/request-id.go')
    changed = ['dto/channel_settings.go', 'dto/openai_response.go', 'service/tiered_settle.go', 'relay/channel/openai/relay_responses.go', 'controller/relay.go', 'service/channel_select.go', 'relay/channel/api_request.go', 'middleware/request-id.go', 'service/http.go', 'model/log.go'] + copied
    subprocess.run(['docker', 'run', '--rm', '-v', str(SOURCE) + ':/src', '--entrypoint', '/usr/local/go/bin/gofmt', 'xtai/issue173-native-builder:verified', '-w'] + ['/src/' + name for name in changed], check=True)
    marker.write_text('\n'.join(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() + '  ' + name for name in sorted(changed)) + '\n')
    print('exact production source backported with anchor and quota-authority checks')


if __name__ == '__main__':
    main()
