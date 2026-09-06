from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from .config import Settings
from .models import CampaignRequest
from .pipeline import GrowthPipeline
from .providers import OmniRouteModel, PageExtractor, SearXNGSearch
from .store import CampaignStore


def print_json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def build(settings: Settings):
    store = CampaignStore(settings.data_dir / "g1.sqlite3")
    store.init()
    model = OmniRouteModel(settings.omniroute_base_url, settings.coding_api_key, settings.reasoning_model,
                           settings.writing_model, settings.fallback_model, store)
    return GrowthPipeline(settings, store, model, SearXNGSearch(settings.searxng_base_url), PageExtractor()), store


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="company-core-g1")
    p.add_argument("--root")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("doctor")
    create = sub.add_parser("create")
    create.add_argument("--objective", choices=["awareness", "education", "walkthrough", "trial"], default="awareness")
    create.add_argument("--topic")
    create.add_argument("--buyer")
    create.add_argument("--no-research", action="store_true")
    create.add_argument("--platform", action="append", choices=["instagram", "x"])
    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    show = sub.add_parser("show")
    show.add_argument("campaign_id")
    runs = sub.add_parser("model-runs")
    runs.add_argument("--limit", type=int, default=20)
    return p


def doctor(settings: Settings, store: CampaignStore) -> dict:
    checks = {"database": {"ok": store.path.exists(), "path": str(store.path)},
              "knowledge": {"ok": (settings.knowledge_dir / "product_claims.json").exists()},
              "coding_api_key": {"ok": bool(settings.coding_api_key)},
              "crawl4ai": {"ok": False}}
    try:
        import crawl4ai
        checks["crawl4ai"] = {"ok": True, "version": getattr(crawl4ai, "__version__", "installed")}
    except ImportError:
        checks["crawl4ai"] = {"ok": False, "reason": "optional; bounded HTML fallback available"}
    for key, url, headers, timeouts in [
        ("searxng", settings.searxng_base_url + "/search?q=logistics&format=json", {}, (10,)),
        ("omniroute", settings.omniroute_base_url + "/models",
         {"Authorization": f"Bearer {settings.coding_api_key}"}, (10, 30)),
    ]:
        last_error = None
        for timeout in timeouts:
            try:
                with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                    checks[key] = {"ok": response.status == 200, "status": response.status}
                break
            except Exception as exc:
                last_error = exc
        else:
            checks[key] = {"ok": False, "error": f"{type(last_error).__name__}: {last_error}"}
    return {"ok": all(item["ok"] for key, item in checks.items() if key != "crawl4ai"), "checks": checks}


def main() -> None:
    args = parser().parse_args()
    settings = Settings.load(args.root)
    pipeline, store = build(settings)
    if args.command == "init":
        settings.data_dir.mkdir(parents=True, exist_ok=True); settings.output_dir.mkdir(parents=True, exist_ok=True)
        print_json({"ok": True, "root": str(settings.root), "database": str(store.path)})
    elif args.command == "doctor":
        print_json(doctor(settings, store))
    elif args.command == "create":
        request = CampaignRequest(objective=args.objective, topic=args.topic, buyer=args.buyer,
                                  fresh_research=not args.no_research, platforms=args.platform or ["instagram", "x"])
        print_json(pipeline.run(request))
    elif args.command == "list":
        print_json(store.recent(args.limit))
    elif args.command == "show":
        value = store.get(args.campaign_id)
        if value is None:
            raise SystemExit(f"campaign not found: {args.campaign_id}")
        print_json(value)
    elif args.command == "model-runs":
        print_json(store.recent_model_runs(args.limit))


if __name__ == "__main__":
    main()
