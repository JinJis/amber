"""Company logos — best-effort brand images for tickers, cached on the datasets volume and served
through the gateway as an **ungoverned passthrough** (like /evidence; not a catalog tool, so no
entitlement — a logo is a decorative brand asset, not licensed data).

Honesty over fake data (CLAUDE §2.6): a logo is NEVER fabricated. A miss returns 204 and the UI
draws a monogram (deterministic initials tile) — we never ship a wrong company's mark or a broken
image.

Hybrid resolver (source chosen by the deployment's keys):
  1. Logo.dev by ticker      — highest quality, needs LOGODEV_TOKEN (US-style symbols)
  2. API Ninjas /v1/logo     — ticker-based incl. KR (.KS/.KQ), needs API_NINJAS_KEY
  3. FMP company profile image — uses the existing FMP key (US, some KR .KS)
  4. domain → Logo.dev/domain (token) or Google favicon — only with a RESOLVED domain, so unknown
     tickers fall through to the monogram instead of a generic globe
Results are cached to `/data/logos/{MARKET}/{TICKER}.{img,meta}`; misses cache a short-lived marker.
"""

from __future__ import annotations

import base64
import binascii
import json
import pathlib
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.http import fetch_json

router = APIRouter(tags=["Logos"])

# magic-byte → content-type sniff for admin uploads (never trust a claimed type)
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),   # RIFF....WEBP
]


def _sniff_image(data: bytes) -> str | None:
    for sig, ct in _MAGIC:
        if data.startswith(sig):
            if ct == "image/webp" and data[8:12] != b"WEBP":
                continue
            return ct
    head = data[:200].lstrip().lower()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return "image/svg+xml"
    return None

_LOGO_DIR = pathlib.Path(settings.evidence_docs_dir).parent / "logos"   # /data/logos
_MAX_BYTES = 2_000_000
_MIN_BYTES = 100
_MISS_TTL = 60 * 60 * 24 * 3          # re-try a missing logo after 3 days (a new logo may appear)
_HIT_CACHE = "public, max-age=604800, immutable"    # 7 days — logos rarely change
_MISS_CACHE = "public, max-age=86400"               # 1 day for a 204

# A compact domain seed for the most-held KR names (no reliable KR logo API; a domain unlocks the
# favicon/logo.dev path). Everything else falls through to the monogram — honest, never a globe.
_KR_DOMAINS: dict[str, str] = {
    "005930": "samsung.com", "000660": "skhynix.com", "373220": "lgensol.com",
    "207940": "samsungbiologics.com", "005380": "hyundai.com", "005490": "posco-inc.com",
    "051910": "lgchem.com", "006400": "samsungsdi.com", "035420": "navercorp.com",
    "035720": "kakaocorp.com", "000270": "kia.com", "068270": "celltrion.com",
    "105560": "kbfg.com", "055550": "shinhangroup.com", "012330": "hyundai-mobis.com",
    "066570": "lge.com", "003670": "poscofuturem.com", "096770": "sk.com",
    "017670": "sktelecom.com", "030200": "kt.com", "015760": "kepco.co.kr",
    "032830": "samsunglife.com", "003550": "lg.com", "086790": "hanafn.com",
    "009150": "samsungsem.com", "011200": "hmm21.com", "010130": "koreazinc.co.kr",
    "028260": "samsungcnt.com", "051900": "lghnh.com", "010950": "sk.com",
    # broader KOSPI/KOSDAQ coverage — the auto path should hit most held names before admin upload
    "005935": "samsung.com", "000810": "samsungfire.com", "018260": "samsungsds.com",
    "029780": "samsungcard.com", "010140": "samsungheavy.com", "016360": "samsungpop.com",
    "207940": "samsungbiologics.com", "034730": "sk.com", "034220": "lgdisplay.com",
    "051905": "lghnh.com", "011070": "lginnotek.com", "108670": "lgvina.com",
    "004990": "lotte.co.kr", "011170": "lottechem.com", "023530": "lotteshopping.com",
    "000100": "yuhan.co.kr", "128940": "hanmi.co.kr", "091990": "celltrion.com",
    "302440": "sdbiosensor.com", "196170": "alteogen.com", "247540": "ecopro.co.kr",
    "086520": "ecopro.co.kr", "066970": "leeno.com", "091700": "partron.co.kr",
    "042700": "hanmisemiconductor.com", "000720": "hdec.co.kr", "028050": "samsungengineering.com",
    "047810": "koreanair.com", "003490": "koreanair.com", "180640": "hanjinkal.com",
    "009540": "ksoe.co.kr", "010620": "hd-hmd.com", "042660": "hanwha-ocean.com",
    "012450": "hanwha.com", "272210": "hanwhasystems.com", "064350": "hyundai-rotem.com",
    "079550": "lignex1.com", "047050": "posco-inter.com", "005387": "hyundai.com",
    "000150": "doosan.com", "034020": "doosanenerbility.com", "241560": "doosanbobcat.com",
    "090430": "amorepacific.com", "051900": "lghnh.com", "097950": "cj.co.kr",
    "001040": "cj.co.kr", "000080": "hitejinro.com", "033780": "ktng.com",
    "139480": "emart.com", "069960": "ehyundai.com", "057050": "homeplus.co.kr",
    "251270": "netmarble.com", "036570": "ncsoft.com", "259960": "krafton.com",
    "263750": "pearlabyss.com", "112040": "wemade.com", "078340": "com2us.com",
    "377300": "kakaopay.com", "323410": "kakaobank.com", "377740": "innoforest.com",
    "086790": "hanafn.com", "316140": "woorifg.com", "138040": "meritzfire.com",
    "024110": "ibk.co.kr", "029530": "shinsegae.com", "004170": "shinsegae.com",
    "161390": "hankooktire.com", "073240": "kumhotire.com", "011210": "hyundai-wia.com",
}


def _norm(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", ticker or "").upper()


def _paths(market: str, ticker: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base = _LOGO_DIR / market.upper() / _norm(ticker)
    return base.with_suffix(".img"), base.with_suffix(".meta"), base.with_suffix(".miss")


def _domain_of(website: str | None) -> str | None:
    if not website:
        return None
    m = re.sub(r"^https?://", "", website.strip(), flags=re.I).split("/")[0]
    m = re.sub(r"^www\.", "", m, flags=re.I).strip().lower()
    return m or None


async def _download_image(url: str, params: dict | None = None) -> tuple[bytes, str] | None:
    """GET an image URL → (bytes, content_type) or None. Rejects non-image / empty / oversized."""
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as c:
            r = await c.get(url, params=params, headers={"User-Agent": "ValueGraph/1.0 (+logos)"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    ct = (r.headers.get("content-type", "").split(";")[0] or "").strip().lower()
    if not ct.startswith("image/"):
        return None
    data = r.content
    if not data or len(data) < _MIN_BYTES or len(data) > _MAX_BYTES:
        return None
    return data, ct or "image/png"


async def _fmp_profile(symbol: str) -> dict | None:
    if not settings.fmp_api_key:
        return None
    try:
        data = await fetch_json("fmp", "https://financialmodelingprep.com/stable/profile",
                                params={"symbol": symbol, "apikey": settings.fmp_api_key})
    except Exception:  # noqa: BLE001 — best-effort; any failure just drops to the next source
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


async def resolve_logo(market: str, ticker: str) -> tuple[bytes, str, str] | None:
    """Hybrid resolve → (bytes, content_type, source) or None. Order favors quality then reach."""
    market = (market or "US").upper()
    sym = (ticker or "").strip()
    if not sym:
        return None
    code = sym.split(".")[0]
    # infer KR from the symbol shape (6-digit code / .KS/.KQ) even when the caller didn't pass a
    # market — citations/artifacts often carry a ticker but no market.
    if re.fullmatch(r"\d{6}", code) or re.search(r"\.K[SQ]$", sym, re.I):
        market = "KR"
    token = settings.logodev_token

    # 1) Logo.dev by ticker (US-style symbols only)
    if token and market == "US" and re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", sym):
        got = await _download_image(f"https://img.logo.dev/ticker/{sym.upper()}",
                                    {"token": token, "size": "200", "format": "png", "retina": "true"})
        if got:
            return got[0], got[1], "Logo.dev"

    # 2) API Ninjas logo by ticker (works for KR codes as 005930.KS/.KQ too)
    if settings.api_ninjas_key:
        syms = [sym]
        if market == "KR" and "." not in sym:
            syms = [f"{code}.KS", f"{code}.KQ"]
        for s_ in syms:
            try:
                data = await fetch_json("api_ninjas", "https://api.api-ninjas.com/v1/logo",
                                        params={"ticker": s_}, headers={"X-Api-Key": settings.api_ninjas_key})
            except Exception:  # noqa: BLE001 — best-effort; fall through to the next source
                break
            img = (data[0].get("image") if isinstance(data, list) and data and isinstance(data[0], dict) else None)
            if img:
                got = await _download_image(str(img))
                if got:
                    return got[0], got[1], "API Ninjas"

    # 3) FMP company profile image (+ website for the domain fallback)
    domain: str | None = None
    if market == "KR":
        domain = _KR_DOMAINS.get(code)
    if domain is None:
        profile = await _fmp_profile(sym if market == "US" else f"{code}.KS")
        if profile:
            if profile.get("image"):
                got = await _download_image(str(profile["image"]))
                if got:
                    return got[0], got[1], "FMP"
            domain = _domain_of(profile.get("website"))

    # 4) domain-based (only with a RESOLVED domain — no domain ⇒ monogram, never a globe)
    if domain:
        if token:
            got = await _download_image(f"https://img.logo.dev/{domain}",
                                        {"token": token, "size": "200", "format": "png"})
            if got:
                return got[0], got[1], f"Logo.dev · {domain}"
        got = await _download_image("https://www.google.com/s2/favicons", {"domain": domain, "sz": "128"})
        if got:
            return got[0], got[1], f"favicon · {domain}"
    return None


def _serve(data: bytes, ct: str) -> Response:
    return Response(content=data, media_type=ct or "image/png", headers={"Cache-Control": _HIT_CACHE})


@router.get("/logos", summary="Company logo (cached, best-effort) — 204 when none (UI draws a monogram)")
async def get_logo(market: str = Query("US"), ticker: str = Query(..., min_length=1)) -> Response:
    market = (market or "US").upper()
    img_p, meta_p, miss_p = _paths(market, ticker)

    if img_p.exists():
        ct = "image/png"
        try:
            ct = json.loads(meta_p.read_text()).get("content_type", ct)
        except (OSError, ValueError):
            pass
        return _serve(img_p.read_bytes(), ct)

    if miss_p.exists() and (time.time() - miss_p.stat().st_mtime) < _MISS_TTL:
        return Response(status_code=204, headers={"Cache-Control": _MISS_CACHE})

    got = await resolve_logo(market, ticker)
    if not got:
        img_p.parent.mkdir(parents=True, exist_ok=True)
        miss_p.write_text("")   # remember the miss (short TTL — a logo may land later)
        return Response(status_code=204, headers={"Cache-Control": _MISS_CACHE})

    data, ct, source = got
    img_p.parent.mkdir(parents=True, exist_ok=True)
    img_p.write_bytes(data)
    meta_p.write_text(json.dumps({"content_type": ct, "source": source, "fetched_at": int(time.time())}))
    miss_p.unlink(missing_ok=True)
    return _serve(data, ct)


class LogoUpload(BaseModel):
    market: str = Field("US")
    ticker: str = Field(..., min_length=1)
    data_url: str = Field(..., description="data:image/*;base64,… or bare base64 (png/jpeg/webp/gif/svg)")


@router.post("/logos", summary="Admin: upload a company logo for a ticker (fills auto-resolve gaps)")
async def upload_logo(body: LogoUpload) -> dict:
    """Manual override for any ticker the hybrid resolver missed (esp. KR). The uploaded image is
    stored in the SAME cache the viewer reads and, being on disk, is never overwritten by the
    resolver/warm pipeline (they skip existing files) — a guaranteed logo for every company."""
    raw = body.data_url.split(",", 1)[1] if body.data_url.startswith("data:") else body.data_url
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "이미지 데이터가 올바르지 않습니다 (base64).")
    ct = _sniff_image(data)
    if not ct:
        raise HTTPException(422, "지원하지 않는 형식입니다 — PNG·JPEG·WEBP·GIF·SVG만 업로드하세요.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "이미지가 너무 큽니다 (최대 2MB).")
    _store(body.market, body.ticker, data, ct, "admin 업로드")
    return {"ok": True, "market": body.market.upper(), "ticker": _norm(body.ticker),
            "content_type": ct, "bytes": len(data)}


def _store(market: str, ticker: str, data: bytes, ct: str, source: str) -> None:
    img_p, meta_p, miss_p = _paths(market, ticker)
    img_p.parent.mkdir(parents=True, exist_ok=True)
    img_p.write_bytes(data)
    meta_p.write_text(json.dumps({"content_type": ct, "source": source, "fetched_at": int(time.time())}))
    miss_p.unlink(missing_ok=True)


async def run_logo_ingest(market: str, tickers: list[str]) -> None:
    """Warm the logo cache for a universe (admin-runnable pipeline, kind `logo`). Best-effort per
    ticker; a miss is recorded so the UI monogram is stable and providers aren't re-hit each view."""
    import asyncio
    import traceback

    from app.store.jobs import finish_job, log_activity, start_job, update_progress

    market = (market or "").upper()
    tickers = tickers or []
    job = start_job("logo", market, f"logo · {len(tickers)} tickers", len(tickers))
    log_activity("logo", market, f"▶ 시작 · {len(tickers)}종목 로고 수집", job_id=job)
    got, miss = 0, 0
    try:
        for i, tk in enumerate(tickers, 1):
            try:
                img_p, _m, miss_p = _paths(market, tk)
                if img_p.exists():
                    got += 1
                else:
                    res = await resolve_logo(market, tk)
                    if res:
                        _store(market, tk, *res)
                        got += 1
                    else:
                        img_p.parent.mkdir(parents=True, exist_ok=True)
                        miss_p.write_text("")
                        miss += 1
            except Exception as exc:  # noqa: BLE001 — one ticker never fails the sweep
                await asyncio.to_thread(log_activity, "logo", market, f"[{tk}] 실패 — {type(exc).__name__}", job, "warn")
            await asyncio.to_thread(update_progress, job, i)
        await asyncio.to_thread(finish_job, job, "success", got, f"{got} 로고 · {miss} 없음(모노그램)")
        await asyncio.to_thread(log_activity, "logo", market, f"✓ 완료 · {got} 로고 · {miss} 없음", job)
    except Exception:  # noqa: BLE001
        await asyncio.to_thread(finish_job, job, "error", got, traceback.format_exc()[-1800:])
