"""Operator-evidenced exact Nody image-reference specifications and quotes."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from catalog import Catalog
from nodyhub import NODY_IMAGE_MODELS, NODY_SOURCE, UUID, money


class NodyImageContracts:
    """An empty configuration keeps image modes unavailable, without affecting text."""

    def __init__(self, revision: str, profiles: tuple[dict, ...]) -> None:
        self.revision = revision
        self.profiles = profiles

    @classmethod
    def load(cls, path: Path | None) -> NodyImageContracts:
        """Load only successful actual-cost evidence for exact documented tuples."""
        if path is None:
            return cls('', ())
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError('Nody image evidence configuration cannot be read') from error
        if not isinstance(raw, dict) or raw.get('schema_version') != 'xtai-nody-image-input-v1':
            raise ValueError('Nody image evidence schema is invalid')
        revision = raw.get('revision')
        rows = raw.get('profiles')
        if not isinstance(revision, str) or not revision or len(revision) > 120 or not isinstance(rows, list) or len(rows) > 21:
            raise ValueError('Nody image evidence revision/profiles are invalid')
        profiles = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Nody image evidence profile must be an object')
            model, mode = row.get('model'), row.get('mode')
            count, duration = row.get('image_count'), row.get('duration')
            resolution = row.get('resolution')
            if (not isinstance(model, str) or model not in NODY_IMAGE_MODELS or type(count) is not int or type(duration) is not int
                    or not 1 <= count <= 7 or (mode, count == 1) not in [('reference', True), ('all_reference', False)]
                    or (resolution, duration) != NODY_IMAGE_MODELS[model]
                    or row.get('status') != 'succeeded' or row.get('cost_status') != 'actual'
                    or row.get('evidence_source') != 'nodyhub_authenticated_video_task'
                    or not isinstance(row.get('evidence_task_id'), str) or not UUID.fullmatch(row['evidence_task_id'])):
                raise ValueError('Nody image evidence is not a verified supported tuple')
            value = row.get('actual_cost_cny_exact')
            if not isinstance(value, str):
                raise ValueError('Nody actual cost must be an exact string')
            try:
                cost = Decimal(value)
            except InvalidOperation as error:
                raise ValueError('Nody actual cost is invalid') from error
            if not cost.is_finite() or cost <= 0 or cost > 100 or cost.as_tuple().exponent < -6:
                raise ValueError('Nody actual cost is invalid')
            identity = (model, resolution, duration, mode, count)
            if identity in seen:
                raise ValueError('Duplicate Nody image evidence tuple')
            seen.add(identity)
            profiles.append({**row, 'actual_cost_cny_exact': money(cost)})
        return cls(revision, tuple(profiles))

    def quote(self, model: str, resolution: str, duration: int, mode: str, count: int) -> dict:
        """Quote only the matching accepted mode/count, never extrapolate asset cost."""
        if type(duration) is not int or type(count) is not int:
            raise ValueError('Invalid image-reference duration/count')
        for row in self.profiles:
            if (row['model'], row['resolution'], row['duration'], row['mode'], row['image_count']) == (model, resolution, duration, mode, count):
                cost = Decimal(row['actual_cost_cny_exact'])
                amount = cost * Decimal('1.5')
                return {'model': model, 'resolution': resolution, 'operation_mode': mode, 'image_count': count,
                        'duration': duration, 'currency': 'CNY', 'billing_unit': 'output_second',
                        'cny_per_second_exact': money(amount / duration), 'amount_cny_exact': money(amount),
                        'output_seconds': duration, 'reference_cost_cny_exact': money(cost),
                        'fallback_multiplier_exact': '1.5', 'pricing_revision': self.revision,
                        'image_contract_evidence': {'task_id': row['evidence_task_id'], 'source': row['evidence_source']},
                        'price_source': NODY_SOURCE, 'contract_version': 'xtai-video-pricing-v1',
                        'input_rate_class': 'without_video_input', 'fallback': False}
        raise ValueError('Image-reference mode/count/spec has no verified price')

    def public_rows(self) -> list[dict]:
        """Return retail-only mode prices; provider task identity and cost stay private."""
        result = []
        for profile in self.profiles:
            quote = self.quote(profile['model'], profile['resolution'], profile['duration'], profile['mode'], profile['image_count'])
            quote.pop('reference_cost_cny_exact')
            quote.pop('image_contract_evidence')
            result.append(quote)
        return result

    def catalog(self, base: Catalog) -> Catalog:
        """Add only evidenced image modes/routes to a new catalog, leaving old data intact."""
        models = []
        for item in base.models:
            profiles = [row for row in self.profiles if row['model'] == item.id]
            if not profiles:
                models.append(item)
                continue
            max_images = max(row['image_count'] for row in profiles)
            resolutions = tuple(dict.fromkeys([*item.resolutions, *(row['resolution'] for row in profiles)]))
            routes = list(item.routes)
            for resolution in resolutions:
                if any(route.provider == 'nodyhub' and route.resolution == resolution for route in routes):
                    continue
                original = next(route for route in item.routes if route.provider == 'nodyhub')
                routes.append(replace(original, resolution=resolution, max_total_assets=max_images))
            routes = [replace(route, max_total_assets=max_images) if route.provider == 'nodyhub' else route for route in routes]
            modes = tuple(dict.fromkeys([*item.operation_modes, *(row['mode'] for row in profiles)]))
            models.append(replace(item, operation_modes=modes, resolutions=resolutions, max_images=max_images, routes=tuple(routes)))
        revision = base.revision + ('+' + self.revision if self.profiles else '')
        return Catalog(base.protocol_version, revision, tuple(models))
