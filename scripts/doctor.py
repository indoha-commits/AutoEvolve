from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def configured(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and value != "GENERATE_ME")


def main() -> int:
    required = ("DASHBOARD_PASSWORD", "SALES_ACTION_TOKEN", "SALES_INTAKE_SECRET")
    missing = [name for name in required if not configured(name)]
    providers = {
        "model router": configured("OMNIROUTE_API_KEY"),
        "Prospeo": configured("PROSPEO_API_KEY"),
        "Lusha": configured("LUSHA_API_KEY"),
        "Hunter": configured("HUNTER_API_KEY"),
        "PDL": configured("PDL_API_KEY"),
        "CompanyEnrich": configured("CE_API_KEY"),
        "Resend": configured("SALES_RESEND_API_KEY"),
    }

    print("Company Core configuration")
    print(f"  required secrets: {'ready' if not missing else 'missing'}")
    for name, ready in providers.items():
        print(f"  {name}: {'configured' if ready else 'optional / disabled'}")

    media_bins = ("MARKETING_G1_BIN", "MARKETING_G2_BIN", "MARKETING_G3_BIN")
    for name in media_bins:
        raw = os.getenv(name, "").strip()
        path = Path(raw).expanduser() if raw else None
        print(f"  {name}: {'ready' if path and path.is_file() else 'optional / unavailable'}")

    if missing:
        print(f"Missing required values: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
