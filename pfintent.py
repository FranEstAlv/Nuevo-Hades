# pfintent.py - DEPRECATED: archivo legado truncado, fuga de cloudscraper sin close
# Se mantiene como shim para no romper imports externos. Delega a payflow.py que ya esta parcheado.
# Si se usa directamente, ahora cierra sesion correctamente.
try:
    from payflow import nbd as _nbd_fixed
    def nbd(card, proxy=None):
        # payflow.nbd ya tiene _close_session en finally (fix 2026-09-21)
        return _nbd_fixed(card, proxy=proxy)
except Exception:
    # fallback minimo si payflow no disponible
    import cloudscraper as _cs
    def nbd(card, proxy=None):
        sess = None
        try:
            sess = _cs.create_scraper(browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, delay=2)
            # legado: no implementado completo - retorna error
            return f"card -» {card}\nStatus -» Error\nResult -» pfintent deprecated, usa payflow.nbd\n"
        finally:
            if sess is not None:
                try:
                    sess.close()
                except Exception:
                    pass
