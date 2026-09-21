# cookie_decodo_corrected.py - Usando Decodo solo para proxy, no para registro

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from typing import Optional, Dict, Any

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ==================== CONFIG ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# SMSPool
SMSPOOL_API_KEY = os.getenv("SMSPOOL_API_KEY", "").strip()
SMSPOOL_BASE_URL = "https://api.smspool.net"
SMSPOOL_COUNTRY = "1"  # USA
SMSPOOL_SERVICE = "39"  # Amazon
SMSPOOL_MAX_PRICE = "0.8"

# Decodo Proxy (para Playwright)
DECODO_USERNAME = os.getenv("DECODO_USERNAME", "smart-Franest90")
DECODO_PASSWORD = os.getenv("DECODO_PASSWORD", "Ktk3YAJVm59Kdg")
DECODO_ENDPOINT = "gate.decodo.com"
DECODO_PORT = "7000"

MAX_CONCURRENT_JOBS = 3
JOB_TIMEOUT = 300

job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
active_jobs: dict[int, float] = {}
_active_lock = asyncio.Lock()
stats = {"total": 0, "success": 0, "failed": 0}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== SMSPOOL ====================
class SMSPoolClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def purchase_sms(self) -> Optional[Dict]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SMSPOOL_BASE_URL}/purchase/sms",
                data={
                    "key": self.api_key,
                    "country": SMSPOOL_COUNTRY,
                    "service": SMSPOOL_SERVICE,
                    "max_price": SMSPOOL_MAX_PRICE,
                    "pricing_option": "0"
                }
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("success") == 1:
                return {
                    "success": True,
                    "order_id": data.get("order_id"),
                    "number": data.get("phonenumber"),
                    "cc": data.get("cc", "1"),
                    "cost": data.get("cost")
                }
            return {"success": False, "error": data.get("type", "UNKNOWN")}
    
    async def check_sms(self, order_id: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SMSPOOL_BASE_URL}/sms/check",
                data={"key": self.api_key, "orderid": order_id}
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("status") == 3:
                return data.get("sms")
            return None
    
    async def cancel_sms(self, order_id: str) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SMSPOOL_BASE_URL}/sms/cancel",
                data={"key": self.api_key, "orderid": order_id}
            )
            return response.status_code == 200 and response.json().get("success") == 1


# ==================== GENERADOR ====================
async def create_amazon_account(status_callback=None) -> Optional[Dict[str, Any]]:
    """Crear cuenta usando Playwright con proxy Decodo"""
    
    async def upd(msg: str):
        if status_callback:
            try:
                await status_callback(msg)
            except Exception:
                pass
    
    # 1. Comprar número
    await upd("📱 Comprando número virtual...")
    sms = SMSPoolClient(SMSPOOL_API_KEY)
    result = await sms.purchase_sms()
    
    if not result or not result.get("success"):
        error = result.get("error", "Desconocido") if result else "Sin respuesta"
        await upd(f"❌ SMSPool: {error}")
        return None
    
    order_id = result.get("order_id")
    phone = f"+{result.get('cc', '1')}{result.get('number', '')}"
    await upd(f"✅ Número: {phone} (Order: {order_id})")
    
    # 2. Generar datos
    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
    fullname = f"{''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()} {''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()}"
    
    # 3. Usar Playwright con proxy Decodo
    await upd("🌐 Conectando con proxy Decodo...")
    
    try:
        from playwright.async_api import async_playwright
        
        # Configurar proxy Decodo - formato correcto
        proxy_username = f"user-{DECODO_USERNAME}-session-{uuid.uuid4().hex[:8]}-country-us"
        proxy_config = {
            "server": f"http://{DECODO_ENDPOINT}:{DECODO_PORT}",
            "username": proxy_username,
            "password": DECODO_PASSWORD
        }
        
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
                )
                
                context = await browser.new_context(
                    proxy=proxy_config,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    timezone_id="America/New_York"
                )
                
                # Anti-detección
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = {runtime: {}};
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                """)
                
                page = await context.new_page()
                
                # Bloquear recursos pesados
                async def block_resources(route):
                    if route.request.resource_type in ("image", "media", "font"):
                        await route.abort()
                    else:
                        await route.continue_()
                await page.route("**/*", block_resources)
                
                # URL CORRECTA de registro
                REGISTER_URL = "https://www.amazon.com/ap/register"
                
                await upd("📝 Cargando página de registro...")
                
                for attempt in range(3):
                    try:
                        await page.goto(REGISTER_URL, wait_until="domcontentloaded", timeout=60000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            raise
                        logger.warning(f"Goto intent {attempt+1}: {e}")
                        await asyncio.sleep(3)
                
                # Esperar formulario
                await page.wait_for_selector("#ap_customer_name", timeout=30000)
                
                await upd("📝 Llenando formulario...")
                await page.fill("#ap_customer_name", fullname)
                await page.fill("#ap_email", phone.replace("+", ""))
                await page.fill("#ap_password", password)
                await page.fill("#ap_password_check", password)
                
                await upd("📤 Enviando...")
                await page.click("#continue")
                
                await upd("📩 Esperando SMS...")
                
                code = None
                for i in range(20):
                    c = await sms.check_sms(order_id)
                    if c:
                        code = c
                        await upd(f"✅ SMS: {code}")
                        break
                    if i % 3 == 0:
                        await upd(f"⏳ Esperando SMS... ({i*10}s)")
                    await asyncio.sleep(10)
                
                if not code:
                    await upd("❌ Sin SMS")
                    await sms.cancel_sms(order_id)
                    return None
                
                await upd("⌨️ Verificando código...")
                try:
                    await page.fill("#auth-pv-enter-code", code)
                    await page.click("#auth-verify-button")
                except Exception:
                    # Intentar con selector alternativo
                    await page.fill('input[name="code"]', code)
                    await page.keyboard.press("Enter")
                
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                
                # Verificar éxito
                url = page.url
                is_logged = await page.query_selector("#nav-link-accountList") is not None
                
                if "your-account" in url or is_logged:
                    await upd("🎉 ¡Cuenta creada!")
                    cookies = await context.cookies()
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                    return {
                        "phone": phone,
                        "password": password,
                        "fullname": fullname,
                        "cookies": cookie_str,
                        "order_id": order_id,
                        "cost": result.get("cost")
                    }
                
                await upd("⚠️ No se verificó la cuenta")
                return None
            finally:
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
            
    except ImportError:
        await upd("❌ Playwright no instalado. Ejecuta: playwright install chromium")
        return None
    except Exception as e:
        logger.exception("Error")
        await upd(f"❌ {str(e)[:90]}")
        return None


# ==================== BOT ====================
async def gcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    async with _active_lock:
        if user_id in active_jobs:
            await update.message.reply_text("⏳ Ya tienes una generación en curso.")
            return
        active_jobs[user_id] = time.time()
        stats["total"] += 1
    
    msg = await update.message.reply_text(
        "🍪 <b>Generando Cookies Amazon US</b>\n\n⏳ Iniciando...",
        parse_mode=ParseMode.HTML
    )
    
    async def upd_status(text: str):
        try:
            await msg.edit_text(f"🍪 <b>Generando Cookies Amazon US</b>\n\n{text}", parse_mode=ParseMode.HTML)
        except Exception:
            pass
    
    try:
        result = await asyncio.wait_for(
            create_amazon_account(status_callback=upd_status),
            timeout=JOB_TIMEOUT
        )
    except asyncio.TimeoutError:
        result = None
    except Exception as e:
        result = None
        logger.error(f"gcookie: {e}")
    finally:
        async with _active_lock:
            active_jobs.pop(user_id, None)
    
    if result:
        cookies = result["cookies"]
        text = (
            f"✅ <b>¡Cuenta creada!</b>\n\n"
            f"👤 <code>{result['fullname']}</code>\n"
            f"📱 <code>{result['phone']}</code>\n"
            f"🔑 <code>{result['password']}</code>\n"
            f"💰 Costo: <code>${result.get('cost', '0')}</code>\n"
            f"🍪 <code>{cookies[:200]}...</code>\n"
            f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await msg.edit_text(text[:4000], parse_mode=ParseMode.HTML)
        stats["success"] += 1
    else:
        await msg.edit_text(
            "❌ <b>Error al generar</b>\n\n"
            "• Verifica SMSPOOL_API_KEY\n"
            "• Verifica DECODO_USERNAME + DECODO_PASSWORD\n"
            "• Verifica saldo en SMSPool\n"
            "• Reintenta en 1-2 minutos",
            parse_mode=ParseMode.HTML
        )
        stats["failed"] += 1


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍪 <b>Bot Generador Cookies Amazon US</b>\n\n"
        "📡 SMSPool + Decodo Proxy\n"
        "<code>/gcookie</code> - Generar cuenta\n"
        "<code>/stats</code> - Estadísticas",
        parse_mode=ParseMode.HTML
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rate = (stats["success"] / stats["total"] * 100) if stats["total"] else 0
    text = (
        f"📊 <b>Estadísticas</b>\n"
        f"Total: <code>{stats['total']}</code>\n"
        f"Éxitos: <code>{stats['success']}</code>\n"
        f"Fallos: <code>{stats['failed']}</code>\n"
        f"Tasa: <code>{rate:.1f}%</code>\n"
        f"Activos: <code>{len(active_jobs)}/{MAX_CONCURRENT_JOBS}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


def main():
    print("""
╔════════════════════════════════════════════╗
║  BOT AMAZON US - SMSPool + Decodo Proxy   ║
║  Con Playwright + Proxy Residencial       ║
╚════════════════════════════════════════════╝
    """)
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Falta TELEGRAM_BOT_TOKEN")
        return
    if not SMSPOOL_API_KEY:
        print("❌ Falta SMSPOOL_API_KEY")
        return
    if not DECODO_USERNAME or not DECODO_PASSWORD:
        print("❌ Falta DECODO_USERNAME o DECODO_PASSWORD")
        return
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("gcookie", gcookie_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    print(f"✅ Bot iniciado | /gcookie /stats")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
