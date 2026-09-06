from __future__ import annotations

import os


def company_name() -> str:
    return os.getenv("COMPANY_NAME", "Company Core").strip() or "Company Core"


def company_public_url() -> str:
    return (
        os.getenv("COMPANY_PUBLIC_URL", "http://localhost:8787").strip()
        or "http://localhost:8787"
    ).rstrip("/")


def company_forms_url() -> str:
    return (
        os.getenv("COMPANY_FORMS_URL", f"{company_public_url()}/contact").strip()
        or f"{company_public_url()}/contact"
    ).rstrip("/")


def company_logo_url() -> str:
    return (
        os.getenv(
            "COMPANY_BRAND_LOGO_URL",
            f"{company_public_url()}/static/company-core-logo.svg",
        ).strip()
        or f"{company_public_url()}/static/company-core-logo.svg"
    )
