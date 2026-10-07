"""NodyHub's verified text-video specifications and protocol adapter."""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
import json
import re
import urllib.error
import urllib.request
from adapters import AdapterError, HttpJsonTransport, JsonResponse, Observation, TransportFailure, VideoAdapter, _observation

NODY_REVISION = 'nody-verified-2026-09-23.1'
NODY_SOURCE = 'verified_upstream_1_5'
# Exact samples: (resolution, tested duration, real CNY cost per output second).
NODY_MODELS = {
    'wan3.0-video': ('480p', 2, '0.375'),
    'wan3.0-video-prime': ('480p', 2, '0.54'),
    'grok-imagine-1.5-video': ('480p', 6, '0.15'),
    'grok-video-3': ('720p', 6, '0.15'),
    'grok-imagine-video-official': ('480p', 1, '0.45'),
    'omni-flash': ('720p', 4, '0.6375'),
    'flux-3-video': ('720p', 5, '1.425'),
}
# V1.0.0 provider API attachment documents image references for these models.
# This is a wire contract only; production admission additionally requires a
# verified per-mode quote and an explicit capability configuration.
NODY_IMAGE_MODELS = {
    'grok-video-3': ('720p', 6),
    'grok-imagine-1.5-video': ('720p', 6),
    'grok-imagine-video-official': ('480p', 1),
}
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

def money(value: Decimal) -> str:
    return format(value.quantize(Decimal('0.000001'), rounding=ROUND_CEILING), 'f')

def verified_row(model: str, resolution: str) -> dict:
    """Public price contains retail only; private source cost stays in quotes."""
    spec = NODY_MODELS.get(model)
    if not spec or resolution != spec[0]:
        raise ValueError('unverified NodyHub model/resolution')
    return {'model':model, 'resolution':resolution, 'currency':'CNY',
            'billing_unit':'output_second', 'cny_per_second_exact':money(Decimal(spec[2])*Decimal('1.5')),
            'pricing_revision':NODY_REVISION,
            'price_source':NODY_SOURCE, 'fallback':False}

def verified_quote(model: str, resolution: str, duration: int) -> dict:
    row = verified_row(model, resolution)
    seconds = int(duration)
    if isinstance(duration, bool) or str(duration) != str(seconds) or seconds != NODY_MODELS[model][1]:
        raise ValueError('invalid NodyHub quote duration')
    cost = Decimal(NODY_MODELS[model][2])*seconds
    return {**row,'contract_version':'xtai-video-pricing-v1','output_seconds':seconds,'fallback_multiplier_exact':'1.5',
            'reference_cost_cny_exact':money(cost), 'amount_cny_exact':money(cost*Decimal('1.5')),
            'input_rate_class':'without_video_input'}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class NodyTransport(HttpJsonTransport):
    """Do not forward provider authorization across redirects."""
    def request_json(self, method, url, *, headers, payload, timeout):
        body=json.dumps(payload).encode() if payload is not None else None
        hs=dict(headers)
        if body is not None:hs['Content-Type']='application/json'
        request=urllib.request.Request(url,data=body,headers=hs,method=method)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            try:
                response=opener.open(request,timeout=timeout)
            except urllib.error.HTTPError as error:
                response=error
            with response:
                raw=response.read(self.max_response_bytes+1);status=response.status;response_headers=dict(response.headers)
        except (OSError,urllib.error.URLError) as error:
            raise TransportFailure(type(error).__name__) from error
        if len(raw)>self.max_response_bytes:raise TransportFailure('response_too_large')
        text=raw.decode('utf-8','replace')
        try:data=json.loads(text)
        except ValueError:data={}
        return JsonResponse(status,response_headers,data if isinstance(data,dict) else {},text[:1000])

def result_url(payload: dict) -> str:
    if isinstance(payload.get('data'), dict):
        payload = payload['data']
    for obj in [payload,payload.get('metadata'),payload.get('video')]:
        if not isinstance(obj,dict):continue
        for key in ['video_url','url']:
            if isinstance(obj.get(key),str) and obj[key]:return obj[key]
    result=payload.get('result')
    videos=result.get('videos') if isinstance(result,dict) else None
    if isinstance(videos,list) and len(videos)==1 and isinstance(videos[0],dict):
        value=videos[0].get('url')
        if isinstance(value,list) and len(value)==1:value=value[0]
        if isinstance(value,str):return value
    return ''

class NodyHubAdapter(VideoAdapter):
    provider_id='nodyhub'
    def __init__(self, config, transport=None):
        super().__init__(config,transport or NodyTransport())

    def request_body(self, upstream_model, payload):
        if payload.get('mode') in {'reference', 'all_reference'}:
            spec = NODY_IMAGE_MODELS.get(upstream_model)
            images = payload.get('images')
            mode = payload['mode']
            if (not spec or (payload.get('resolution'), payload.get('duration')) != spec
                    or payload.get('aspect_ratio') != '16:9'
                    or payload.get('generate_audio') is not True
                    or not isinstance(images, list)
                    or not (1 <= len(images) <= 7)
                    or (mode == 'reference' and len(images) != 1)
                    or (mode == 'all_reference' and len(images) < 2)
                    or any(payload.get(k) for k in ('videos', 'audios'))
                    or any(not isinstance(item, dict) or item.get('role') != 'reference'
                           or not isinstance(item.get('url'), str) or not item['url'].startswith('https://')
                           for item in images)):
                raise AdapterError('nodyhub_unverified_spec', 'Unsupported Nody image-reference specification.', phase='validate', http_status=400)
            urls = [item['url'] for item in images]
            body = {'model': upstream_model, 'prompt': payload['prompt'], 'duration': spec[1]}
            if upstream_model == 'grok-video-3':
                body.update(resolution='720P', images=urls)
                if len(urls) > 1:
                    body['ratio'] = '16:9'
            elif upstream_model == 'grok-imagine-1.5-video':
                body.update(quality='720p', image_urls=urls)
                if len(urls) > 1:
                    body['size'] = '16:9'
            else:
                body['resolution'] = spec[0]
                if len(urls) == 1:
                    body['image'] = {'url': urls[0]}
                else:
                    body['reference_images'] = [{'url': url} for url in urls]
                    body['aspect_ratio'] = '16:9'
            return body
        spec=NODY_MODELS.get(upstream_model)
        if (not spec or payload.get('resolution')!=spec[0] or payload.get('duration')!=spec[1]
            or payload.get('aspect_ratio')!='16:9' or payload.get('mode')!='text'
            or any(payload.get(k) for k in ['images','videos','audios'])):
            raise AdapterError('nodyhub_unverified_spec','Only verified text-video specifications are enabled.',phase='validate',http_status=400)
        if payload.get('generate_audio') is not True:
            raise AdapterError('nodyhub_audio_mode_unsupported','Only retaining the verified default upstream audio is enabled; generate_audio must be true.',phase='validate',http_status=400)
        body={'model':upstream_model,'prompt':payload['prompt']}
        if upstream_model=='grok-video-3':body.update(seconds=str(spec[1]),size='1280x720')
        else:body.update(duration=spec[1],resolution=spec[0],aspect_ratio='16:9')
        return body

    def submit(self, request_id, upstream_model, payload):
        body=self.request_body(upstream_model,payload)
        path='/v1/videos' if upstream_model=='grok-video-3' and payload.get('mode')=='text' else '/v2/videos/generations'
        try:
            response=self.transport.request_json('POST',self.config.base_url.rstrip('/')+path,headers=self._headers(request_id),payload=body,timeout=self.config.submit_timeout_seconds)
        except TransportFailure as error:
            raise AdapterError('nodyhub_submit_uncertain','Submit outcome unknown; do not replay.',phase='submit',uncertain=True,retryable=True) from error
        self._raise_submit_http(response)
        observed=_observation(response.payload)
        if not UUID.fullmatch(observed.upstream_task_id):
            raise AdapterError('nodyhub_submit_identity_unknown','Missing or invalid gateway task UUID; do not replay.',phase='submit',uncertain=True)
        return observed

    def poll(self, upstream_task_id):
        if not UUID.fullmatch(upstream_task_id):
            raise AdapterError('nodyhub_task_id_invalid','Invalid stored task UUID.',phase='poll',uncertain=True)
        try:
            response=self.transport.request_json('GET',self.config.base_url.rstrip('/')+'/v1/videos/'+upstream_task_id,headers=self._headers(''),payload=None,timeout=self.config.poll_timeout_seconds)
        except TransportFailure as error:
            raise AdapterError('nodyhub_poll_unavailable','Query original task again.',phase='poll',uncertain=True,retryable=True) from error
        if response.status>=400 or not response.payload:
            raise AdapterError('nodyhub_poll_unavailable','Query original task again.',phase='poll',http_status=response.status,uncertain=True,retryable=True)
        received = response.payload.get('data')
        normalized=dict(received if isinstance(received, dict) and received.get('status') else response.payload)
        normalized['id']=upstream_task_id;normalized['task_id']=upstream_task_id
        normalized['video_url']=result_url(response.payload)
        observed=_observation(normalized,fallback_task_id=upstream_task_id)
        if observed.status=='succeeded' and not observed.result_url:
            raise AdapterError('nodyhub_result_missing','Successful task result is not yet available.',phase='deliver',uncertain=True,retryable=True)
        return replace(observed,upstream_task_id=upstream_task_id)
