# Research

- Downstream source of truth: `XTAI_VIDEO_REFERENCE_VIDEO_PROTOCOL_V22(1).md`, candidate freeze dated 2026-08-13.
- The downstream document is a proposal, not a mandatory contract. We retain its safe canonical fields and idempotency rules but replace unnecessary pricing and capability restrictions with the user's relay policy.
- Official Volcano Engine material confirms Seedance 2.0 supports mixed text/image/video/audio input, up to 9 images, 3 videos and 3 audios, with output up to 15 seconds. Official product material identifies MP3/WAV audio and MP4 video; exact API route limits still require adapter verification.
- Existing gateway asynchronously persists payloads before upstream submission. Persisting a signed URL query would violate v2.2, so paid v2.2 cannot be enabled until a durable URL-free registration/refresh design exists.
- The common contract supports MP3 and WAV, while each route is eligible only for the formats it verifies.
- Pricing follows the relay rule: reservation is the matching Ark official input-mode rate ×1.5 and final settlement is upstream trusted net cost ×1.5. Ark currently publishes standard/Fast/Mini rates of 28/22/14 CNY per million tokens with video input and 46/37/23 without video input. Audio does not add a guessed multiplier.
