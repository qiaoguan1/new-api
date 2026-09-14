# Banana Chat-Image Adapter

This isolated adapter preserves the public OpenAI image-generation contract while calling the
chat-multimodal endpoints exposed by the validated Banana providers.

## Routes

- `banana-flash`: Haina vip, with Rolldek fallback only after a known fast capacity rejection.
- `banana-pro`: Rolldek only.

Timeouts, transport failures, successful responses without an image, and other uncertain outcomes
are never replayed because the first provider may already have billed or generated the image.

## Security

- The service accepts a file-backed internal bearer token.
- The Nginx location also denies clients outside localhost, the Docker bridge, and the production
  host address.
- Provider keys are read from regular 0600 files and never logged.
- The container runs as UID/GID 10001, read-only, with every Linux capability dropped.
- Prompts, response bodies, image URLs, and generated image bodies are never logged.
- Only one image and URL response mode are supported.

## Local tests

```sh
python -m unittest discover -s tests -v
```

Copy `env.example` to `.env` only on the deployment host and point it at private secret files. Do
not commit `.env` or any key. The three mounted key files must remain mode 0600 and be owned by
UID/GID 10001 so the non-root container can read them.
