"""
Reading an owner's existing website, safely.

The address comes from the owner, so it is treated as untrusted:
- only http(s) on ports 80/443, no user:password@ addresses, at most 4 redirects (each checked again);
- every host is resolved first and refused if any address is private, loopback, link-local, multicast or reserved
  (no reaching into the hub's own network), and the connection is then made to that checked address — so a DNS answer
  can't change between the check and the request;
- only addresses on the public internet (not private, shared, documentation or other special ranges);
- at most 2 MB per page, HTML only, 10 seconds per request and 20 seconds for a whole answer however slowly it trickles in;
- we ask for an uncompressed answer; a gzip/deflate one is unpacked in small steps and stopped at the size limit (so a
  tiny "zip bomb" can't blow up into gigabytes), and any other packing is refused.
Tests and a laptop may read private addresses (settings.fetch_private_ok).
"""
import asyncio
import ipaddress
import re
import socket
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import settings

MAX_BYTES = 2_000_000
TIMEOUT = 10
DEADLINE = 20          # seconds for one whole fetch (redirects and body included)
UA = "Mozilla/5.0 (compatible; BusinessAIActionHub/1.0; +https://employz.ai)"


class FetchError(Exception):
    """A plain sentence the owner can act on."""


@dataclass
class Page:
    url: str
    html: str
    https: bool
    bytes: int


def normalise_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        raise FetchError("Enter your website address, like www.yourbusiness.in")
    bad = FetchError("That doesn't look like a website address.")
    if re.match(r"^https?://", s, re.I):
        pass
    elif re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", s, re.I):   # ftp:, javascript:, file:, mailto: … (but not "site.in:80")
        raise bad
    else:
        s = "https://" + s
    try:
        u = urlparse(s)
        port = u.port
    except ValueError:
        raise bad
    if u.scheme.lower() not in ("http", "https") or not u.hostname or "@" in (u.netloc or "") or len(s) > 500 or any(c in s for c in " \t\r\n\\"):
        raise bad
    host = u.hostname.lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not settings.fetch_private_ok and _blocked(str(literal)):
        raise FetchError("We can't read that address. Enter your public website, like www.yourbusiness.in")
    local = settings.fetch_private_ok and (host == "localhost" or (literal is not None and _blocked(str(literal))))
    if port not in (None, 80, 443) and not local:
        raise bad
    if "." not in host and not local and literal is None:
        raise bad
    return s


async def _addresses(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise FetchError("We couldn't find that website. Check the address and try again.")
    return sorted({i[4][0] for i in infos})


def _blocked(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified
            or not a.is_global)        # also shared (100.64/10), documentation and benchmark ranges


async def _one(client: httpx.AsyncClient, url: str, accept: str = "text/html,application/xhtml+xml") -> httpx.Response:
    u = urlparse(url)
    host = u.hostname.lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
        ips = [str(literal)]
    except ValueError:
        ips = await _addresses(host)
    if not ips:
        raise FetchError("We couldn't find that website. Check the address and try again.")
    if not settings.fetch_private_ok and any(_blocked(ip) for ip in ips):
        raise FetchError("We can't read that address. Enter your public website, like www.yourbusiness.in")
    ip = ips[0]
    netloc = f"[{ip}]" if ":" in ip else ip
    if u.port:
        netloc += f":{u.port}"
    pinned = u._replace(netloc=netloc).geturl()
    headers = {"Host": host + (f":{u.port}" if u.port else ""), "User-Agent": UA, "Accept": accept, "Accept-Encoding": "identity"}
    req = client.build_request("GET", pinned, headers=headers, extensions={"sni_hostname": host} if u.scheme == "https" else {})
    return await client.send(req, stream=True)


TOO_SLOW = "That website took too long to answer. Try again, or fill the form instead."


def _unpacker(resp: httpx.Response):
    enc = (resp.headers.get("content-encoding") or "").strip().lower()
    if enc in ("", "identity"):
        return None
    if enc in ("gzip", "x-gzip", "deflate"):
        return zlib.decompressobj(zlib.MAX_WBITS | 32)          # gzip or zlib header, found automatically
    raise FetchError("That website sent its page packed in a way we can't read.")


async def _body(resp: httpx.Response, max_bytes: int, too_big: Exception | None = None) -> bytes:
    """At most `max_bytes` of the answer. Raw bytes are read and, when packed, unpacked at most max_bytes+1 at a time, so a
    small packed answer can never grow past the limit in memory. With `too_big`, a larger answer raises it instead."""
    unpack = _unpacker(resp)
    out = bytearray()
    try:
        async for raw in resp.aiter_raw():
            data = raw
            while data:
                if unpack is None:
                    out += data
                    data = b""
                else:
                    out += unpack.decompress(data, max_bytes + 1 - len(out))
                    data = unpack.unconsumed_tail
                if len(out) > max_bytes:
                    break
            if len(out) > max_bytes:
                if too_big is not None:
                    raise too_big
                break
    except zlib.error:
        raise FetchError("That website sent a page we couldn't unpack.")
    return bytes(out[:max_bytes])


async def _within(coro, message: str = TOO_SLOW):
    """The whole fetch — every redirect and the body — must finish within DEADLINE seconds."""
    try:
        async with asyncio.timeout(DEADLINE):
            return await coro
    except TimeoutError:
        raise FetchError(message)


async def fetch(raw_url: str, client: httpx.AsyncClient | None = None) -> Page:
    return await _within(_fetch(raw_url, client))


async def _fetch(raw_url: str, client: httpx.AsyncClient | None = None) -> Page:
    url = normalise_url(raw_url)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, verify=True)
    try:
        for _ in range(5):
            try:
                resp = await _one(client, url)
            except httpx.ConnectError as e:
                if url.startswith("https://") and "certificate" not in str(e).lower():
                    url = "http://" + url[len("https://"):]  # some small-business sites still have no https
                    continue
                raise FetchError("We couldn't open that website. Check the address and try again.")
            except httpx.HTTPError:
                raise FetchError("That website took too long to answer. Try again, or fill the form instead.")
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                await resp.aclose()
                url = normalise_url(urljoin(url, resp.headers["location"]))
                continue
            try:
                if resp.status_code >= 400:
                    raise FetchError("That website didn't open for us (it answered with an error). Try the home page address.")
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype.lower():
                    raise FetchError("That address isn't a web page. Enter your website's home page.")
                body = await _body(resp, MAX_BYTES)
            except httpx.HTTPError:
                raise FetchError(TOO_SLOW)
            finally:
                await resp.aclose()
            return Page(url=url, html=body.decode(_charset(resp), errors="replace"), https=url.startswith("https://"), bytes=len(body))
        raise FetchError("That website redirects too many times.")
    finally:
        if own:
            await client.aclose()


IMAGE_TYPES = ("image/png", "image/jpeg")


def _charset(resp: httpx.Response) -> str:
    import codecs
    name = resp.charset_encoding or "utf-8"
    try:
        codecs.lookup(name)
        return name
    except LookupError:
        return "utf-8"


async def fetch_image(raw_url: str, max_bytes: int = 800_000) -> tuple[bytes, str]:
    """A small PNG or JPEG (a website's logo), with the same checks as a page: public addresses only, every redirect checked
    again, at most `max_bytes`. SVG and other formats are refused (they can't be drawn on a design safely)."""
    return await _within(_fetch_image(raw_url, max_bytes), "The logo took too long to open.")


async def _fetch_image(raw_url: str, max_bytes: int) -> tuple[bytes, str]:
    url = normalise_url(raw_url)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, verify=True) as client:
        for _ in range(5):
            try:
                resp = await _one(client, url, accept="image/png,image/jpeg")
            except httpx.HTTPError:
                raise FetchError("The logo couldn't be opened.")
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                await resp.aclose()
                url = normalise_url(urljoin(url, resp.headers["location"]))
                continue
            try:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code >= 400 or ctype not in IMAGE_TYPES:
                    raise FetchError("The logo isn't a PNG or JPG.")
                body = await _body(resp, max_bytes, too_big=FetchError("The logo file is too large."))
            except httpx.HTTPError:
                raise FetchError("The logo couldn't be opened.")
            finally:
                await resp.aclose()
            from .files import sniff
            real = sniff(body)
            if real not in IMAGE_TYPES:      # the header said PNG/JPEG; the bytes must say so too
                raise FetchError("The logo isn't a PNG or JPG.")
            return body, real
        raise FetchError("The logo address redirects too many times.")


@dataclass
class Text:
    url: str
    status: int
    content_type: str
    text: str


async def fetch_text(raw_url: str, client: httpx.AsyncClient | None = None, max_bytes: int = 500_000,
                     accept: str = "text/plain,*/*;q=0.5") -> Text:
    """A small text file (like robots.txt) with every check a page gets: public addresses only, at most 4 redirects each
    checked again, at most `max_bytes`, DEADLINE seconds in all. The body is read only for a 2xx answer — the caller
    decides what an error status means. Network trouble raises FetchError."""
    return await _within(_fetch_text(raw_url, client, max_bytes, accept))


async def _fetch_text(raw_url: str, client: httpx.AsyncClient | None, max_bytes: int, accept: str) -> Text:
    url = normalise_url(raw_url)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, verify=True)
    try:
        for _ in range(5):
            try:
                resp = await _one(client, url, accept=accept)
            except httpx.HTTPError:
                raise FetchError("We couldn't open that website.")
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                await resp.aclose()
                url = normalise_url(urljoin(url, resp.headers["location"]))
                continue
            try:
                ctype = resp.headers.get("content-type", "")
                body = await _body(resp, max_bytes) if 200 <= resp.status_code < 300 else b""
            except httpx.HTTPError:
                raise FetchError(TOO_SLOW)
            finally:
                await resp.aclose()
            return Text(url=url, status=resp.status_code, content_type=ctype, text=body.decode("utf-8-sig", errors="replace"))
        raise FetchError("That website redirects too many times.")
    finally:
        if own:
            await client.aclose()


# ───────────────────────── reading a page ─────────────────────────
PHONE = re.compile(r"(?:\+?91[\s-]?)?(?<!\d)[6-9]\d{4}[\s-]?\d{5}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class Facts:
    url: str
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    whatsapp: str = ""
    logo: str = ""
    colour: str = ""
    links: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    u = urljoin(base, href.strip())
    return u if u.startswith(("http://", "https://")) else ""


def read(page: Page) -> Facts:
    soup = BeautifulSoup(page.html, "html.parser")
    f = Facts(url=page.url)
    f.title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]
    desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find("meta", attrs={"property": "og:description"})
    f.description = (desc.get("content") or "")[:400] if desc else ""
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and re.fullmatch(r"#[0-9a-fA-F]{6}", (theme.get("content") or "").strip()):
        f.colour = theme["content"].strip()
    logo = None
    for img in soup.find_all("img", limit=60):
        hint = " ".join([img.get("alt") or "", " ".join(img.get("class") or []), img.get("src") or "", img.get("id") or ""]).lower()
        if "logo" in hint:
            logo = img.get("src") or img.get("data-src")
            break
    if not logo:
        og = soup.find("meta", attrs={"property": "og:image"})
        logo = og.get("content") if og else None
    if not logo:
        icon = soup.find("link", rel=lambda r: r and any(x in ("apple-touch-icon", "icon") for x in (r if isinstance(r, list) else [r])))
        logo = icon.get("href") if icon else None
    f.logo = _abs(page.url, logo)[:500]
    for a in soup.find_all("a", href=True, limit=400):
        href = a["href"].strip()
        low = href.lower()
        if "wa.me/" in low or "api.whatsapp.com" in low or "whatsapp://" in low:
            m = re.search(r"(\d{10,13})", href)
            if m and not f.whatsapp:
                f.whatsapp = m.group(1)
        elif low.startswith("tel:"):
            f.phones.append(re.sub(r"[^\d+]", "", href[4:]))
        elif low.startswith("mailto:"):
            f.emails.append(href[7:].split("?")[0])
        else:
            text = a.get_text(" ", strip=True).lower()
            for key in ("about", "service", "product", "contact"):
                if key not in f.links and (key in low or key in text):
                    u = _abs(page.url, href)
                    if u and urlparse(u).hostname == urlparse(page.url).hostname:
                        f.links[key] = u
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    f.headings = [h.get_text(" ", strip=True)[:140] for h in soup.find_all(["h1", "h2", "h3"], limit=30) if h.get_text(strip=True)]
    body = soup.get_text("\n", strip=True)
    f.text = re.sub(r"\n{2,}", "\n", body)[:12000]
    f.phones += PHONE.findall(body)
    f.emails += EMAIL.findall(body)
    f.phones = list(dict.fromkeys(re.sub(r"\D", "", p)[-10:] for p in f.phones if len(re.sub(r"\D", "", p)) >= 10))[:5]
    f.emails = list(dict.fromkeys(e.lower() for e in f.emails if not e.lower().endswith((".png", ".jpg", ".webp"))))[:3]
    html_low = page.html.lower()
    f.checks = {
        "https": page.https,
        "mobile": bool(soup.find("meta", attrs={"name": "viewport"})),
        "title": bool(f.title) and bool(f.description),
        "contact": bool(f.phones or f.whatsapp or f.emails),
        "whatsapp": bool(f.whatsapp),
        "form": bool(BeautifulSoup(page.html, "html.parser").find("form")),
        "proof": any(w in html_low for w in ("testimonial", "review", "what our clients", "what our customers", "happy customers", "rated")),
        "light": page.bytes < 1_500_000,
    }
    return f


async def read_site(raw_url: str) -> Facts:
    """The home page, plus about/services/contact pages when the home page links to them (text only, best effort)."""
    home = await fetch(raw_url)
    facts = read(home)
    extra = []
    for key in ("about", "service", "product", "contact"):
        u = facts.links.get(key)
        if not u or u == home.url:
            continue
        try:
            sub = read(await fetch(u))
        except FetchError:
            continue
        extra.append(f"--- {key.upper()} PAGE ---\n{sub.text[:4000]}")
        facts.phones = list(dict.fromkeys(facts.phones + sub.phones))[:5]
        facts.emails = list(dict.fromkeys(facts.emails + sub.emails))[:3]
        facts.whatsapp = facts.whatsapp or sub.whatsapp
        if sub.checks.get("form"):
            facts.checks["form"] = True
        facts.checks["contact"] = facts.checks["contact"] or sub.checks["contact"]
        facts.checks["whatsapp"] = bool(facts.whatsapp)
    if extra:
        facts.text = (facts.text[:8000] + "\n" + "\n".join(extra))[:20000]
    return facts
