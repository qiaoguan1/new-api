# Upstream Video Model Normalization

**Issue**: [#34](https://github.com/qiaoguan1/new-api/issues/34)

## Goal

Continuously discover changing video model names from multiple upstreams and
map only reviewed, unambiguous variants to the fixed Xingtou relay catalog.
The downstream protocol and application remain unchanged.

## Fixed relay catalog

| Stable model | Variant | Allowed resolutions |
|---|---|---|
| `seedance-2.0` | full | `480p`, `720p`, `1080p` |
| `seedance-2.0-fast` | fast | `480p`, `720p` |
| `seedance-2.0-mini` | mini | `480p`, `720p` |

Resolution is a separate capability dimension. It is never embedded into the
stable downstream model identifier.

## Requirements

1. Pull every enabled video upstream catalog on a schedule and retain the raw
   model name, source slug, observed time, response fingerprint, and collection
   status without logging credentials.
2. A failed or malformed collection must not replace the source's last complete
   snapshot.
3. Resolve names in this precedence order: reviewed source-specific exact
   alias, reviewed global exact alias, reviewed regex rule, conservative token
   parser. Every rule has an ID, version, priority, enabled flag, authoring
   reason, and review state.
4. Automatic parsing may accept a result only when exactly one allowed stable
   model and one allowed resolution are present. Missing resolution, conflicting
   variant tokens, unknown family, or ambiguous marketing labels fail closed.
5. AI may propose a candidate mapping and explanation for a newly discovered
   name. AI output is untrusted input and cannot approve a rule, change a price,
   enable a route, or enter the publish manifest.
6. Multiple upstream raw models may map to the same stable SKU. They remain
   distinct route candidates with source-specific health, price evidence, and
   original request model values.
7. A route is publishable only when it has an approved mapping, an enabled
   source route, a passing health result, a versioned trusted price, and an
   explicit publish-policy entry. The intersection is recalculated atomically.
8. Initially only `seedance-2.0` with `720p` is enabled for publishing. Other
   catalog entries remain defined but hidden until every gate is satisfied and
   the policy is deliberately changed.
9. `value-sd-premium-720p`, `sd2-pro-720p`, and `sd2-720p` resolve to
   `seedance-2.0` + `720p`; `sd2-fast-*` and `sd2-mini-*` resolve to their
   corresponding variants and resolutions. Unknown `video_value` values are
   rejected.
10. Price evidence is keyed by source plus raw model and then attributed to the
    stable SKU through the approved mapping. Missing trustworthy actual-cost
    evidence must never be guessed and must not overwrite production pricing.
11. Operators can edit and validate rule/publish-policy JSON without changing
    Python code. Invalid configuration fails closed and keeps the last valid
    published manifest.
12. The public manifest exposes stable model, resolution, availability, and
    protocol version only. It never exposes upstream names, source names,
    credentials, internal route IDs, cost, margin, or review notes.

## Operational outcomes

- A changed upstream name appears in a review queue during the next collection.
- An approved rule can be activated on the next run without a NewAPI rebuild.
- Removing or disabling a route removes it from the next atomic manifest while
  preserving its audit history.
- Re-running the same complete inputs is deterministic and idempotent.

## Non-goals

- Changing the Xingtou AI desktop application or its UI.
- Normalizing text or image model catalogs in this issue.
- Automatically publishing a name based solely on AI confidence.
- Manufacturing paid video requests to obtain pricing evidence.
