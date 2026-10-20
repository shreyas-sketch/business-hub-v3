"""
Where each member website lives.

With SITES_DOMAIN set (e.g. employz.ai), every website is <name>.employz.ai: one wildcard DNS record and one wildcard
certificate cover every owner, so nobody touches DNS. Without it (a laptop, tests), websites live at APP_URL/s/<name>.
An owner's own domain (Growth Mentorship and up) is answered the same way once the admin marks it live.

Requests that arrive on a website's host are rewritten to the hub's /s/<name>/... routes, so only that website's pages
are reachable there: the hub's API and pages never answer on a member website's address.
"""
import re

from .config import settings

SITE_PATHS = ("", "/", "/inquiry", "/guide", "/guide/get", "/wa", "/card", "/card.vcf")
MAGNET_PATH = re.compile(r"^/guide/[a-z0-9-]{1,60}(/file|/cover\.png)?$")   # uploaded lead magnets: form, download, cover


def site_url(slug: str, path: str = "") -> str:
    if settings.sites_domain:
        return f"https://{slug}.{settings.sites_domain}{path}"
    return f"{settings.app_url}/s/{slug}{path}"


def slug_from_host(host: str) -> str | None:
    """'shree-ganesh.employz.ai' → 'shree-ganesh'. None for the hub's own host, reserved names and anything else."""
    host = (host or "").strip().lower().rstrip(".").split(":", 1)[0]
    sd = settings.sites_domain
    if not sd or not host.endswith("." + sd) or host == settings.app_host:
        return None
    label = host[: -len(sd) - 1]
    if not label or "." in label or label in settings.reserved_subdomains:
        return None
    return label


def rewrite_to_site(scope: dict, slug: str) -> bool:
    """Points a website-host request at /s/<slug>/... Returns False for paths a website doesn't have."""
    path = scope.get("path") or "/"
    if path.rstrip("/") not in {p.rstrip("/") for p in SITE_PATHS} and not MAGNET_PATH.match(path.rstrip("/")):
        return False
    tail = "" if path in ("", "/") else path
    scope["path"] = f"/s/{slug}{tail}"
    scope["raw_path"] = scope["path"].encode()
    scope["site_base"] = ""           # links on the page are relative to the website's own host
    return True


def base_for(request, slug: str) -> str:
    """'' when the page is served on the website's own host, '/s/<slug>' when served under the hub."""
    return "" if request.scope.get("site_base") == "" else f"/s/{slug}"
