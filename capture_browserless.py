"""Captura tráfico HTTP de un navegador remoto de Browserless."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo


def decode_payload(raw):
    """Conserva bytes sin pérdida y añade texto/JSON cuando sea posible."""
    if raw is None:
        return None
    value = {'base64': base64.b64encode(raw).decode('ascii'), 'size_bytes': len(raw)}
    try:
        value['text'] = raw.decode('utf-8')
    except UnicodeDecodeError:
        return value
    try:
        value['json'] = json.loads(value['text'])
    except ValueError:
        pass
    return value


def graphql_operations(url, payload, content_type):
    params = parse_qs(urlsplit(url).query, keep_blank_values=True)
    candidates = []
    if payload and 'json' in payload:
        body = payload['json']
        candidates.extend(body if isinstance(body, list) else [body])
    if payload and 'application/graphql' in content_type:
        candidates.append({'query': payload.get('text', '')})
    if payload and 'application/x-www-form-urlencoded' in content_type:
        form = parse_qs(payload.get('text', ''), keep_blank_values=True)
        candidates.append({key: values[0] for key, values in form.items()})
    if params:
        candidates.append({key: values[0] for key, values in params.items()})
    operations = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if not any(key in candidate for key in ('query', 'operationName', 'extensions')):
            continue
        operation = {key: candidate[key] for key in ('query', 'operationName', 'variables', 'extensions') if key in candidate}
        for key in ('variables', 'extensions'):
            if isinstance(operation.get(key), str):
                try:
                    operation[key] = json.loads(operation[key])
                except ValueError:
                    pass
        operations.append(operation)
    return operations


def manual_capture(context, page, seconds, result):
    """Mantiene el bucle de Playwright activo durante el control por LiveURL."""
    cdp = context.new_cdp_session(page)
    stop = threading.Event()
    def live_complete(*_):
        result['manual_stop_reason'] = 'live_viewer_closed'
        stop.set()
    cdp.on('Browserless.liveComplete', live_complete)
    live = cdp.send('Browserless.liveURL', {
        'interactable': True, 'showBrowserInterface': True,
        'timeout': max(1, int(seconds * 1000)),
    })
    if live.get('error') or not live.get('liveURL'):
        result['capture_error'] = 'LiveURLUnavailable'
        print('Browserless no creó el enlace interactivo. Revisa límites del plan y timeout.', file=sys.stderr)
        return False
    print('Abre este enlace privado en tu navegador (no lo compartas):', flush=True)
    print(live['liveURL'], flush=True)
    print(f'Capturando hasta {seconds:.0f} segundos. Pulsa Enter aquí para guardar y cerrar.', flush=True)
    if sys.stdin.isatty():
        def read_enter():
            try:
                input()
            except EOFError:
                return
            result['manual_stop_reason'] = 'enter'
            stop.set()
        threading.Thread(target=read_enter, daemon=True).start()
    deadline = time.monotonic() + seconds
    while not stop.is_set() and time.monotonic() < deadline:
        # input() en el hilo principal impediría despachar los eventos de red.
        page.wait_for_timeout(min(250, max(1, (deadline - time.monotonic()) * 1000)))
    result.setdefault('manual_stop_reason', 'time_limit')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--vancouver-tomorrow', action='store_true',
                        help='Abre disponibilidad de un tour de Vancouver y consulta mañana (hora local).')
    parser.add_argument('--manual', action='store_true', help='Muestra un LiveURL para navegar manualmente mientras captura.')
    parser.add_argument('--session-timeout', type=int, default=300, help='Límite total en segundos de la sesión manual (sujeto al plan).')
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--output', type=Path, default=Path('traffic.json'))
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in {'http', 'https'} or args.seconds < 0:
        parser.error('Usa una URL http(s) y --seconds mayor o igual que cero.')
    if args.manual and (args.seconds <= 0 or args.session_timeout <= 0):
        parser.error('El modo manual requiere --seconds y --session-timeout positivos.')
    env_values = {}
    try:
        from dotenv import dotenv_values
        env_values = dotenv_values(Path(__file__).resolve().parent / '.env')
    except ImportError:
        pass
    token = next((str(value).strip() for value in (
        os.environ.get('BROWSERLESS_TOKEN'), os.environ.get('BROWSERLESS_API_KEY'),
        env_values.get('BROWSERLESS_TOKEN'), env_values.get('BROWSERLESS_API_KEY'),
    ) if value and str(value).strip()), '')
    if not token:
        parser.error('Define BROWSERLESS_TOKEN o BROWSERLESS_API_KEY; para leer .env instala python-dotenv.')
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        parser.exit(1, 'Instala Playwright: python -m pip install playwright\n')

    events = []
    result = {'url': args.url, 'cookies': [], 'events': events}
    requests = {}
    sockets = []

    def request_id(req):
        if req not in requests:
            requests[req] = len(requests) + 1
        return requests[req]

    def record(item):
        item['timestamp'] = datetime.now(timezone.utc).isoformat()
        events.append(item)

    def read_field(item, key, getter):
        try:
            item[key] = getter()
        except Error as exc:
            item.setdefault('capture_errors', {})[key] = type(exc).__name__

    def on_request(req):
        item = {'type': 'request', 'id': request_id(req), 'url': req.url,
                'method': req.method, 'resource_type': req.resource_type,
                'query_params': parse_qs(urlsplit(req.url).query, keep_blank_values=True)}
        record(item)
        read_field(item, 'headers', req.all_headers)
        read_field(item, 'headers_array', req.headers_array)
        read_field(item, 'payload', lambda: decode_payload(req.post_data_buffer))
        payload = item.get('payload')
        item['post_data'] = payload.get('text') if payload else None
        item['graphql'] = graphql_operations(req.url, payload, item.get('headers', {}).get('content-type', ''))
        item['graphql_like'] = bool(item['graphql']) or 'graphql' in req.url.lower()
        if req.redirected_from:
            item['redirected_from_id'] = request_id(req.redirected_from)

    def on_response(res):
        item = {'type': 'response', 'id': request_id(res.request),
                'url': res.url, 'status': res.status, 'status_text': res.status_text}
        record(item)
        read_field(item, 'headers', res.all_headers)
        read_field(item, 'headers_array', res.headers_array)

    def on_finished(req):
        item = {'type': 'response_body', 'id': request_id(req), 'url': req.url}
        record(item)
        read_field(item, 'timing', lambda: req.timing)
        read_field(item, 'sizes', req.sizes)
        res = req.response()
        if res is not None:
            read_field(item, 'payload', lambda: decode_payload(res.body()))
            payload = item.get('payload')
            item['body'] = payload.get('text') if payload else None

    def watch_page(page):
        def on_socket(ws):
            socket_id = len(sockets) + 1
            sockets.append(ws)
            record({'type': 'websocket', 'socket_id': socket_id, 'url': ws.url})
            def frame(direction, value):
                raw = value.encode('utf-8') if isinstance(value, str) else value
                record({'type': direction, 'socket_id': socket_id, 'url': ws.url,
                        'payload': decode_payload(raw),
                        'frame_type': 'text' if isinstance(value, str) else 'binary'})
            ws.on('framesent', lambda value: frame('websocket_sent', value))
            ws.on('framereceived', lambda value: frame('websocket_received', value))
            ws.on('close', lambda: record({'type': 'websocket_closed', 'socket_id': socket_id}))
        page.on('websocket', on_socket)

    def check_tomorrow(page):
        target = datetime.now(ZoneInfo('America/Vancouver')).date() + timedelta(days=1)
        availability = {'date': target.isoformat(), 'timezone': 'America/Vancouver',
                        'status': 'opening', 'afternoon_from': '13:00'}
        result['availability'] = availability
        # El widget se inicializa después de la carga inicial del documento.
        page.wait_for_timeout(8000)
        page.get_by_role('button', name='Check Availability', exact=True).click()
        tomorrow = page.locator('button').filter(has_text=re.compile(r'^\s*' + str(target.day) + r'\s+Tomorrow\b'))
        tomorrow.click(timeout=30000)
        page.wait_for_timeout(3000)
        dialog = page.get_by_role('dialog').filter(has=tomorrow)
        availability['visible_text'] = dialog.inner_text()
        slots = []
        for radio in dialog.get_by_role('radio').all():
            label = radio.get_attribute('aria-label') or ''
            match = re.search(r'(\d{1,2}):(\d{2})\s*([ap])\.?m', label, re.I)
            if not match:
                continue
            hour = int(match[1]) % 12 + (12 if match[3].lower() == 'p' else 0)
            slots.append({'label': label, 'time': f'{hour:02}:{int(match[2]):02}',
                          'enabled': radio.is_enabled(), 'afternoon': hour >= 13})
        availability['slots'] = slots
        availability['status'] = ('afternoon_listed' if any(s['afternoon'] and s['enabled'] for s in slots)
                                  else 'no_afternoon_listed' if slots else 'no_times_detected')

    def on_failed(req):
        record({'type': 'requestfailed', 'id': request_id(req),
                       'url': req.url, 'method': req.method, 'error': req.failure})

    code = 0
    try:
        with sync_playwright() as p:
            connection_params = {'token': token}
            if args.manual:
                connection_params['timeout'] = args.session_timeout * 1000
            session_started = time.monotonic()
            endpoint = 'wss://production-sfo.browserless.io?' + urlencode(connection_params)
            browser = p.chromium.connect_over_cdp(endpoint)
            context = None
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                context.on('request', on_request)
                context.on('response', on_response)
                context.on('requestfinished', on_finished)
                context.on('requestfailed', on_failed)
                context.on('page', watch_page)
                for existing_page in context.pages:
                    watch_page(existing_page)
                page = context.new_page()
                page.goto(args.url, wait_until='domcontentloaded', timeout=60000)
                if args.vancouver_tomorrow:
                    check_tomorrow(page)
                if args.manual:
                    remaining = args.session_timeout - (time.monotonic() - session_started) - 5
                    if remaining <= 0:
                        result['capture_error'] = 'ManualSessionTimeExhausted'
                        code = 1
                    elif not manual_capture(context, page, min(args.seconds, remaining), result):
                        code = 1
                else:
                    page.wait_for_timeout(args.seconds * 1000)
            finally:
                try:
                    if context is not None:
                        result['cookies'] = context.cookies()
                finally:
                    browser.close()
    except KeyboardInterrupt:
        result['capture_error'] = 'Captura interrumpida.'
        code = 130
    except Error as exc:
        # No imprimir la excepción: puede incluir la URL con el token.
        result['capture_error'] = type(exc).__name__
        print('Falló la sesión. Comprueba token, conexión, URL y límites de Browserless.', file=sys.stderr)
        code = 1
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(f'Guardados {len(events)} eventos en {args.output}')
    return code


if __name__ == '__main__':
    sys.exit(main())
