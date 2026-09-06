# Model routing

Company Core talks to AI models through the OpenAI Chat Completions protocol. All agents use the
same base URL and API key, while role variables select the model used for each workload.

## OmniRoute

OmniRoute is the recommended setup when you want multiple providers, route aliases, and fallback
behind one local endpoint.

```bash
npm install -g omniroute
omniroute
```

The dashboard and API start on port `20128`. In the OmniRoute dashboard, connect at least one
provider and copy the key shown under **Endpoints**. Configure Company Core:

```dotenv
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_API_KEY=replace-with-your-endpoint-key
MODEL_FAST=auto/best-fast
MODEL_REASONING=auto/best-reasoning
MODEL_FREE=auto/best-free
MODEL_CODING=auto/best-coding
MODEL_VISION=auto/best-vision
CODING_BASE_URL=http://127.0.0.1:20128/v1
CODING_API_KEY=replace-with-your-endpoint-key
CODING_DEFAULT_MODEL=auto/best-coding
```

```bash
curl http://127.0.0.1:20128/v1/models \
  -H "Authorization: Bearer replace-with-your-endpoint-key"
```

Start OmniRoute before Company Core. If they run in separate Docker containers, `127.0.0.1` points
to the current container; use a shared Compose service name instead.

## Ollama and Llama

Ollama is the simplest fully local option. Install Ollama using its platform instructions, then:

```bash
ollama pull llama3.2
ollama serve
curl http://127.0.0.1:11434/v1/models
```

Configure Company Core with the exact name returned by `ollama list` or `/v1/models`:

```dotenv
OMNIROUTE_BASE_URL=http://127.0.0.1:11434/v1
OMNIROUTE_API_KEY=ollama
MODEL_FAST=llama3.2
MODEL_REASONING=llama3.2
MODEL_FREE=llama3.2
MODEL_CODING=llama3.2
MODEL_VISION=llama3.2
CODING_BASE_URL=http://127.0.0.1:11434/v1
CODING_API_KEY=ollama
CODING_DEFAULT_MODEL=llama3.2
```

The API key is a non-secret placeholder required by OpenAI-compatible clients. Choose a
vision-capable installed model for `MODEL_VISION` if you use image analysis. Small local models may
produce campaign packages that fail strict quality gates; review and regenerate those outputs.

## Other OpenAI-compatible endpoints

For LiteLLM or a hosted provider, use its `/v1` base URL, API key, and exact model identifiers:

```dotenv
OMNIROUTE_BASE_URL=https://your-gateway.example/v1
OMNIROUTE_API_KEY=replace-with-your-private-key
MODEL_FAST=provider/model-name
MODEL_REASONING=provider/model-name
MODEL_FREE=provider/model-name
MODEL_CODING=provider/model-name
MODEL_VISION=provider/vision-model-name
```

Never expose the model API key in frontend code or commit it to Git. Run `make doctor` after editing
`.env`, then use the provider's models endpoint or a minimal chat-completions request to verify the
connection before launching long media workflows.
