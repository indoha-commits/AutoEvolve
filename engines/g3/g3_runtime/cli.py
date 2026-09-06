from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .buffer import BufferClient
from .errors import G3Error
from .handoff import load_handoff
from .ledger import Ledger
from .r2 import R2Uploader
from .service import submit_handoff


def client_from_env() -> BufferClient:
    return BufferClient(
        os.environ.get("BUFFER_API_KEY", ""),
        os.environ.get("BUFFER_API_ENDPOINT", "https://api.buffer.com"),
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="company-core-g3")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("account", help="show Buffer organizations")
    channels = commands.add_parser("channels", help="list connected Buffer channels")
    channels.add_argument("--organization-id")
    doctor = commands.add_parser("doctor", help="verify Buffer, R2 configuration, and channel IDs")
    doctor.add_argument("--require", action="append", choices=["x", "instagram"], default=[])
    validate = commands.add_parser("validate", help="validate a G2-to-G3 handoff without network access")
    validate.add_argument("handoff")
    validate.add_argument("--asset-root")
    draft = commands.add_parser("draft", help="upload media to R2 and create Buffer drafts only")
    draft.add_argument("handoff")
    draft.add_argument("--asset-root")
    draft.add_argument("--ledger", default="state/g3.sqlite3")
    draft.add_argument("--dry-run", action="store_true")
    return root


def _organization(client: BufferClient, provided: str | None) -> tuple[dict, str]:
    account = client.account()
    organizations = account.get("organizations") or []
    organization_id = (provided or os.environ.get("BUFFER_ORGANIZATION_ID", "")).strip()
    if not organization_id:
        if len(organizations) != 1:
            raise G3Error("set BUFFER_ORGANIZATION_ID (the account does not have exactly one organization)")
        organization_id = str(organizations[0]["id"])
    return account, organization_id


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            handoff = load_handoff(args.handoff, args.asset_root)
            result = {"ok": True, "campaign_id": handoff.campaign_id,
                      "drafts": len(handoff.entries), "draft_only": True}
        elif args.command == "account":
            result = client_from_env().account()
        elif args.command == "channels":
            client = client_from_env()
            _, organization_id = _organization(client, args.organization_id)
            result = {"organization_id": organization_id, "channels": client.channels(organization_id)}
        elif args.command == "doctor":
            client = client_from_env()
            account, organization_id = _organization(client, None)
            channels = client.channels(organization_id)
            usable = [item for item in channels if not item.get("isDisconnected") and not item.get("isLocked")]
            by_service = {str(item.get("service")): item for item in usable}
            requested_service = {"x": "twitter", "instagram": "instagram"}
            missing = [service for service in args.require if requested_service[service] not in by_service]
            configured = {
                "x": bool(os.environ.get("BUFFER_X_CHANNEL_ID", "").strip()),
                "instagram": bool(os.environ.get("BUFFER_INSTAGRAM_CHANNEL_ID", "").strip()),
            }
            R2Uploader()
            result = {"ok": not missing, "account": account.get("email"),
                      "organization_id": organization_id, "usable_channels": usable,
                      "channel_ids_configured": configured, "missing": missing,
                      "r2": "configured", "draft_only": True}
            if missing:
                print(json.dumps(result, indent=2))
                return 2
        else:
            handoff = load_handoff(args.handoff, args.asset_root)
            client = BufferClient("DRY_RUN") if args.dry_run else client_from_env()
            uploader = None if args.dry_run else R2Uploader()
            result = submit_handoff(client, uploader, Ledger(args.ledger), handoff, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    except G3Error as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
