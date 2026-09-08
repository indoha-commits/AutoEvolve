<p align="center">
  <img src="static/company-core-logo.svg" width="72" alt="Company Core logo">
</p>

<h1 align="center">Company Core</h1>

<p align="center">
  Self-hosted sales and marketing operations, from first signal to approved outreach.
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-67d9a7"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-4aa7ff">
  <img alt="FastAPI" src="https://img.shields.io/badge/API-FastAPI-009688">
  <img alt="Self-hosted" src="https://img.shields.io/badge/deployment-self--hosted-101e2a">
</p>

Company Core is a self-hosted sales and marketing operations workspace. It combines lead intake,
prospect research, contact enrichment, email drafting and delivery, campaign production, media
assembly, publishing handoff, approval queues, and activity history in one FastAPI application.

The repository includes the complete application and its G1/G2/G3 marketing engines. External
providers are optional: configure only the integrations you intend to use.

## Product at a glance

Company Core gives operators a browser-based cockpit instead of scattering research, enrichment,
drafts, approvals, and campaign state across unrelated tools.

### Discover companies and resolve the right contact

Search a market by industry and location, keep one candidate per company, and resolve contact data
only when a company is worth pursuing. This keeps prospecting costs controlled while preserving the
source and research context used for outreach.

![Company Core lead discovery workspace](docs/assets/company-core-lead-discovery.png)

### Turn company intelligence into an approved action

Enriched company context, contact readiness, generated drafts, delivery events, and follow-up state
remain attached to the lead. Sales and marketing actions stay reviewable before anything reaches an
external provider.

![Company Core company intelligence workspace](docs/assets/company-core-lead-intelligence.png)

The same cockpit also handles marketing campaign direction, scripts, scene assets, carousel and
video uploads, Buffer draft handoff, attribution, and operating history. See the
[product tour](docs/product-tour.md) for the page-by-page workflow.

## Quick start

Requirements: Python 3.12+, GNU Make, OpenSSL, and FFmpeg. Python 3.12 is required by the bundled
OpenHands coding SDK. Node.js is only needed for the optional
Cloudflare email Worker.

On Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install python3.12 python3.12-venv python3-pip make openssl ffmpeg
```

On macOS with Homebrew:

```bash
brew install python@3.12 make openssl ffmpeg
```

```bash
git clone https://github.com/indoha-commits/GrowthRail.git
cd GrowthRail
make setup
```

If `python3` is older than 3.12 but `python3.12` is installed, run
`make setup PYTHON=python3.12`.

`make setup` creates isolated virtual environments, installs the application and media engines,
copies safe configuration templates, and generates local authentication secrets. It never
overwrites an existing `.env`. Next, choose an AI backend.

### Option A: OmniRoute

Use OmniRoute when you want one local endpoint with model routing and provider fallback. It requires
Node.js 22.22.2 or newer.

```bash
npm install -g omniroute
omniroute
```

Open <http://localhost:20128>, configure a provider, and copy the endpoint API key shown by
OmniRoute. Put it in Company Core's `.env`:

```dotenv
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_API_KEY=replace-with-your-omniroute-endpoint-key
MODEL_FAST=auto/best-fast
MODEL_REASONING=auto/best-reasoning
MODEL_FREE=auto/best-free
MODEL_CODING=auto/best-coding
MODEL_VISION=auto/best-vision

CODING_BASE_URL=http://127.0.0.1:20128/v1
CODING_API_KEY=replace-with-your-omniroute-endpoint-key
CODING_DEFAULT_MODEL=auto/best-coding
```

Confirm the gateway before starting Company Core:

```bash
curl http://127.0.0.1:20128/v1/models \
  -H "Authorization: Bearer replace-with-your-omniroute-endpoint-key"
```

### Option B: Ollama with a local Llama model

Use Ollama when you want inference to remain local and do not need OmniRoute's provider routing.
Install Ollama, pull a model that fits your machine, and start its server:

```bash
ollama pull llama3.2
ollama serve
```

Ollama exposes an OpenAI-compatible endpoint. Set every role to an actual installed model name;
OmniRoute's `auto/*` aliases do not exist in Ollama.

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

Verify with `curl http://127.0.0.1:11434/v1/models`. You can use different installed models for
reasoning, coding, and vision when available.

### Start Company Core

```bash
make doctor
make dev
```

Open <http://localhost:8787> and sign in with `DASHBOARD_USER` and `DASHBOARD_PASSWORD` from your
private `.env`. API documentation is available at <http://localhost:8787/docs>.

If `make doctor` reports optional providers as disabled, startup can continue. Model-backed actions
require the selected AI endpoint; prospecting, email, media search, publishing, and calendar actions
require only their corresponding optional integrations.

## Add your brand assets

The repository intentionally contains no original product showcase, branded outro, or private logo
binary. Configure your own assets before generating publishable media:

1. Replace `static/company-core-logo.svg` or point `COMPANY_BRAND_LOGO_URL` to a public HTTPS logo.
   Use a PNG for outbound email because email-client SVG support is inconsistent.
2. Put an owned product screenshot or video under `engines/g2/assets/references/` and set
   `MARKETING_G2_SHOWCASE` to its path. This appears in the product showcase scene.
3. Put an owned end-card image under the same directory and set `MARKETING_G2_OUTRO` to its path.
   A portrait 1080x1920 image is recommended for short-form video.
4. Update the approved company facts and claims in `engines/g1/knowledge/` before generating a
   campaign. G1 treats these files as the source of truth for product copy.

The placeholder paths `replace-with-your-showcase.mp4` and `replace-with-your-outro.png` do not
exist by design. This prevents a new installation from publishing another company's branding.

## Capabilities

- Sales: website and campaign lead intake, company discovery, contact resolution, enrichment,
  qualification, editable AI email drafts, approval, Resend delivery, reply events, and meeting
  tracking.
- Marketing: campaign planning, carousel and video asset workflows, AI scene planning, Pexels and
  Pixabay retrieval, voice options, caption variants, manual asset upload, and Buffer handoff.
- Operations: unified queues, status filters, direct actions, project workspaces, health checks,
  and immutable history views.
- Research and coding: web research, repository intelligence, and optional OpenHands-backed coding
  operations through any OpenAI-compatible model gateway.

## Configuration

Edit `.env` after setup. The dashboard runs without provider keys; a feature reports its missing
configuration only when invoked. See [configuration](docs/configuration.md) for the provider map,
[model routing](docs/model-routing.md) for OmniRoute, Ollama, and direct OpenAI-compatible setup,
[lead intake](docs/lead-intake.md) for form contracts, and [deployment](docs/deployment.md) for
reverse proxy and Cloudflare Tunnel examples.

Never commit `.env`, SQLite files, generated media, provider payloads, contact exports, or agent
conversation history. The supplied `.gitignore` excludes these by default.

## Tutorials and operations

- [First sales outreach](docs/first-outreach.md): installation check through approved Resend delivery.
- [First marketing campaign](docs/first-campaign.md): brief through media review and Buffer draft.
- [Provider setup](docs/providers.md): minimum keys, feature mapping, and credit-saving behavior.
- [Product tour](docs/product-tour.md): what each operator page is responsible for.
- [Troubleshooting](docs/troubleshooting.md): common HTTP, model, media, and tunnel failures.
- [Production checklist](docs/production-checklist.md): security and deliverability checks before launch.
- [Upgrading and backups](docs/upgrading.md): safe update, validation, backup, and rollback process.

## Project layout

```text
agents/                         founder, growth, research, sales, and coding agents
app/                            FastAPI routes, authentication, intake, and webhooks
core/                           state, SQLite stores, projects, branding, and configuration
services/                       sales providers, email, media, monitoring, and workers
engines/g1                      campaign and content package generation
engines/g2                      media acquisition, voice, and rendering
engines/g3                      Buffer publishing handoff and R2 media hosting
templates/ + static/            operator dashboard
company-sales-email-ingress/    optional Cloudflare inbound-email Worker
config/                         local service and workspace registry
data/ + projects/               private runtime state, never committed
docs/                           setup and integration guides
docs/assets/                    GitHub product illustrations
```

## Development

```bash
make test
make lint
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the MIT [LICENSE](LICENSE).
