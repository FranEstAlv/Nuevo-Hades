import asyncio
import sqlite3  # Añade esta líneaaa..
import random
import csv
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from faker import Faker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import math
from gates_api import gate_check, gate_check_multiple
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)
from credit_card_generator import CreditCardGenerator
from amazon_api import amazon_check, amazon_check_multiple  # type: ignore
from key_manager import KeyManager
# === INICIO IMPORTACIÓN ATLANTIC ===
from atlantic_client import atlantic_check, atlantic_check_multiple
# === FIN IMPORTACIÓN ATLANTIC ===

# === INICIO IMPORTACIÓN PAYPAL CRONOS ===
from paypal_optimized import paypal_gate_check
# === FIN IMPORTACIÓN PAYPAL CRONOS ===

# === INICIO BLOQUE COOKIES ===
import asyncpg
import aiohttp
import uuid
from telegram.ext import CallbackQueryHandler
# === FIN BLOQUE COOKIES ===

# Definición de planes y restricciones
PLANES = {
    'OLIMPO': {
        'descripcion': 'Acceso Total',
        'restricciones': [],  # Sin restricciones (solo no admin)
        'nivel': 4
    },
    '1MES': {
        'descripcion': 'Acceso Total (30 días)',
        'restricciones': [],  # Igual que OLIMPO
        'nivel': 4
    },
    '15DIAS': {
        'descripcion': 'Plan Intermedio',
        'restricciones': ['di', 'dimass', 'ph', 'phmass'],  # Sin Euridice ni Philotes
        'nivel': 2
    },
    '1SEMA': {
        'descripcion': 'Plan Básico',
        'restricciones': ['di', 'dimass', 'ph', 'phmass', 'eu', 'eumass', 'wo', 'womass'],  # Solo básicos + Amazon
        'nivel': 1
    }
}

# CONFIGURACIÓN DE GATES PARA MENÚ CON BOTONES
GATES_CONFIG = {
    'atlantic': {
        'name': '🌊 Atlantida',  
        'description': 'Verificador Atlantida ',
        'command': 'at',
        'status': 'online',
        'emoji': '🌊',
        'plan_required': ['OLIMPO', '1MES', '15DIAS']
    },
    'amazon': {
        'name': '🛒 Amazon',
        'description': 'Verificador de tarjetas para Amazon',
        'command': 'az',
        'status': 'online',
        'emoji': '🛒',
        'plan_required': ['OLIMPO', '1MES', '15DIAS', '1SEMA']  # Todos pueden usarlo
    },
    'euridice': {
        'name': '🔱 Euridice',
        'description': 'Verificador Euridice (100 MXN)',
        'command': 'di',
        'status': 'online',
        'emoji': '🔱',
        'plan_required': ['OLIMPO', '1MES', '15DIAS']
    },
    'philotes': {
        'name': '💎 Philotes',
        'description': 'Verificador Philotes (Solo asociación)',
        'command': 'ph',
        'status': 'online',
        'emoji': '💎',
        'plan_required': ['OLIMPO', '1MES']
    },
    'eurinias': {
        'name': '⚡ Eurinias',
        'description': 'Verificador Eurinias (20 MXN)',
        'command': 'eu',
        'status': 'online',
        'emoji': '⚡',
        'plan_required': ['OLIMPO', '1MES']
    },
    'wojtek': {
        'name': '🔥 Wojtek',
        'description': 'Verificador Wojtek (50 MXN)',
        'command': 'wo',
        'status': 'online',
        'emoji': '🔥',
        'plan_required': ['OLIMPO', '1MES']
    },
    'cronos': {
        'name': '⏳ Cronos',
        'description': 'Cronos Paypal $0.10 USD - Ideal para ccs',
        'command': 'cr',
        'status': 'online',
        'emoji': '⏳',
        'plan_required': ['OLIMPO', '1MES']
    }
}

# Configuración de paginación
GATES_PER_PAGE = 5

# Comandos premium (solo OLIMPO y 1MES)
COMANDOS_PREMIUM = ['ph', 'phmass', 'eu', 'eumass', 'wo', 'womass']
# Comandos estándar (OLIMPO, 1MES, 15DIAS)
COMANDOS_ESTANDAR = ['di', 'dimass']


# --- CAMBIO 1: CARGAR TOKEN DESDE VARIABLES DE ENTORNO ---
import os

# Verificación exhaustiva del volumen
def ensure_directories():
    """Crear directorios para archivos de datos (CSV, etc.)"""
    directories = ["data", "data/txt", "data/gates"]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

ensure_directories()


from dotenv import load_dotenv

# Cargar las variables del archivo .env (para pruebas locales)
load_dotenv()

# Obtener el token desde las variables de entorno
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Archivos necesarios (CSV, etc.) en /app/data (se suben a Git)
DATA_DIR = "/app/data"

# SOLO POSTGRESQL - Railway
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL no configurada. Asegúrate de tener la variable en el archivo .env")

print("🔌 Conectando a PostgreSQL...")
key_manager = KeyManager(db_file=DATABASE_URL)
print("✅ Conexión establecida")

# Asegurarse de que el token existe
if not TOKEN:
    raise ValueError("No se encontró el TELEGRAM_TOKEN. Asegúrate de configurarlo en Railway.")

generator = CreditCardGenerator()
fake = Faker()




# Variable global para almacenar las cookies de Amazon por usuario
amazon_cookies = {}

# Variables para controlar el tiempo entre ejecuciones de comandos específicos
last_az_time = {}  # Diccionario para registrar la última ejecución de /az por usuario
last_azmass_time = {}  # Diccionario para registrar la última ejecución de /azmass por usuario
AZ_COOLDOWN = 7  # Tiempo mínimo en segundos entre ejecuciones de /az
AZMASS_COOLDOWN = 20  # Tiempo mínimo en segundos entre ejecuciones de /azmass

# Variables para control de concurrencia
user_request_count = defaultdict(int)  # Contador de peticiones por usuario
MAX_USER_REQUESTS = 3  # Máximo de peticiones simultáneas por usuario

# Variables para controlar las peticiones por usuario
user_lock = asyncio.Lock()  # Lock global para operaciones con diccionarios
user_processing = {}  # Diccionario para rastrear usuarios en proceso
last_request_time = {}  # Diccionario para registrar la última petición de cada usuario
MIN_TIME_BETWEEN_REQUESTS = 5  # Tiempo mínimo en segundos entre peticiones


import re

def sanitize_gate_response(message):
    """Limpia mensajes de error para no exponer URLs o detalles técnicos"""
    if not message:
        return "Error en el proceso"
    
    # Ocultar URLs completas
    message = re.sub(r'https?://[^\s"\']+', '[REDACTED]', message)
    
    # Ocultar dominios específicos
    message = re.sub(r'accounts\.theatlantic\.com', '[REDACTED]', message)
    message = re.sub(r'theatlantic\.com', '[REDACTED]', message)
    
    # Reemplazar errores técnicos de Playwright
    replacements = {
        r'Page\.goto:.*': 'Error de conexión con el gate',
        r'navigating to.*': '',
        r'Call log:.*': '',
        r'Timeout \d+ms exceeded': 'Tiempo de espera agotado',
        r'waiting until "[^"]+"': '',
        r'waiting for "[^"]+"': '',
        r'frame\.navigate:.*': 'Error al cargar el formulario',
        r'net::ERR_[A-Z_]+': 'Error de red',
        r'Execution context was destroyed.*': 'Sesión interrumpida',
        r'Target closed.*': 'Conexión cerrada',
        r'Protocol error.*': 'Error interno',
    }
    
    for pattern, replacement in replacements.items():
        message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)
    
    # Limpiar líneas vacías múltiples y espacios
    message = re.sub(r'\n\s*\n', '\n', message)
    message = re.sub(r'\s+', ' ', message)
    
    # Si queda muy largo o es técnico, mensaje genérico
    if len(message) > 200 or 'Traceback' in message or 'Exception' in message:
        return "Error en el proceso de verificación"
    
    return message.strip() or "Error desconocido"

# ============ PERSISTENCIA DE SUPERADMINS ============

def init_superadmins_table():
    """Crea la tabla de superadmins si no existe"""
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        # Detectar si es PostgreSQL o SQLite
        if key_manager.postgres_url:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS superadmins (
                    user_id BIGINT PRIMARY KEY,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_creator BOOLEAN DEFAULT FALSE
                )
            ''')
            # Insertar el creador principal si no existe
            cursor.execute('''
                INSERT INTO superadmins (user_id, added_by, is_creator)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (user_id) DO NOTHING
            ''', (5531198491, 5531198491))
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS superadmins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_creator BOOLEAN DEFAULT 0
                )
            ''')
            # Insertar el creador principal si no existe
            cursor.execute('''
                INSERT OR IGNORE INTO superadmins (user_id, added_by, is_creator)
                VALUES (?, ?, 1)
            ''', (5531198491, 5531198491))
        
        conn.commit()
        conn.close()
        print("✅ Tabla de superadmins inicializada")
    except Exception as e:
        print(f"⚠️ Error inicializando tabla superadmins: {e}")
        
        

# === INICIO FUNCIONES DB COOKIES ===
async def init_cookie_db():
    """Inicializa las tablas de cookies en PostgreSQL"""
    global cookie_db_pool
    cookie_db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=5, max_size=20)
    async with cookie_db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cookie_users (
                user_id BIGINT PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                credits INT DEFAULT 0,
                created_date TIMESTAMP DEFAULT NOW(),
                last_used TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS cookie_transactions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id BIGINT NOT NULL,
                admin_id TEXT,
                amount INT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('add', 'use', 'refund')),
                date TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_cookie_transactions_user_id ON cookie_transactions(user_id);
        """)
        # Asegurar que el superadmin exista en cookie_users
        await conn.execute(
            "INSERT INTO cookie_users (user_id, credits) VALUES ($1, 9999) ON CONFLICT (user_id) DO NOTHING",
            COOKIE_SUPER_ADMIN
        )
    print("✅ Tablas de cookies inicializadas")

async def get_cookie_user(user_id):
    async with cookie_db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cookie_users WHERE user_id = $1", user_id)
        if not row:
            # Verificar si tiene transacciones aunque no esté en cookie_users
            trans = await conn.fetchrow(
                """
                SELECT 
                    COALESCE(SUM(CASE WHEN type = 'add' THEN amount ELSE 0 END), 0) -
                    COALESCE(SUM(CASE WHEN type = 'use' THEN amount ELSE 0 END), 0) as real_credits
                FROM cookie_transactions 
                WHERE user_id = $1
                """,
                user_id
            )
            real_credits = trans['real_credits'] if trans else 0
            
            now = datetime.now()
            await conn.execute(
                "INSERT INTO cookie_users (user_id, username, first_name, credits, created_date, last_used) VALUES ($1, $2, $3, $4, $5, $6)",
                user_id, "", "", real_credits, now, now
            )
            return {"user_id": user_id, "username": "", "first_name": "", "credits": real_credits}
        
        # Verificar que los créditos coincidan con las transacciones (opcional, para corregir inconsistencias)
        trans = await conn.fetchrow(
            """
            SELECT 
                COALESCE(SUM(CASE WHEN type = 'add' THEN amount ELSE 0 END), 0) -
                COALESCE(SUM(CASE WHEN type = 'use' THEN amount ELSE 0 END), 0) as real_credits
            FROM cookie_transactions 
            WHERE user_id = $1
            """,
            user_id
        )
        
        credits_from_trans = trans['real_credits'] if trans else 0
        current_credits = row['credits']
        
        # Si hay discrepancia, corregir (opcional)
        if credits_from_trans != current_credits:
            await conn.execute(
                "UPDATE cookie_users SET credits = $1 WHERE user_id = $2",
                credits_from_trans, user_id
            )
            row = dict(row)
            row['credits'] = credits_from_trans
        
        return dict(row)

async def add_cookie_credits(user_id, amount, admin_id):
    async with cookie_db_pool.acquire() as conn:
        async with conn.transaction():
            # PRIMERO: Asegurar que el usuario existe en cookie_users
            await conn.execute(
                """
                INSERT INTO cookie_users (user_id, username, first_name, credits, created_date, last_used)
                VALUES ($1, '', '', 0, NOW(), NOW())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id
            )
            
            # AHORA SÍ: Actualizar los créditos
            await conn.execute(
                "UPDATE cookie_users SET credits = credits + $1, last_used = NOW() WHERE user_id = $2",
                amount, user_id
            )
            
            # Registrar la transacción
            await conn.execute(
                "INSERT INTO cookie_transactions (user_id, admin_id, amount, type, date) VALUES ($1, $2, $3, 'add', NOW())",
                user_id, str(admin_id), amount
            )

async def use_cookie_credits(user_id, amount):
    async with cookie_db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT credits FROM cookie_users WHERE user_id = $1 FOR UPDATE", user_id)
            if row and row['credits'] >= amount:
                await conn.execute("UPDATE cookie_users SET credits = credits - $1, last_used = NOW() WHERE user_id = $2", amount, user_id)
                await conn.execute(
                    "INSERT INTO cookie_transactions (user_id, amount, type, date) VALUES ($1, $2, 'use', NOW())",
                    user_id, amount
                )
                return True
            return False

async def is_cookie_admin(user_id):
    # Reutiliza la verificación de admin existente
    return key_manager.is_admin(user_id) or user_id == COOKIE_SUPER_ADMIN
# === FIN FUNCIONES DB COOKIES ===

# === INICIO FUNCIONES AUDITORÍA ADMIN ===
async def init_audit_table():
    """Inicializa tabla de auditoría de acciones de admin"""
    async with cookie_db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                admin_id BIGINT NOT NULL,
                action_type TEXT NOT NULL,
                target_id BIGINT,
                details TEXT,
                timestamp TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_admin_id ON admin_audit_log(admin_id);
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON admin_audit_log(timestamp);
        """)
    print("✅ Tabla de auditoría inicializada")

async def log_admin_action(admin_id: int, action_type: str, target_id: int = None, details: str = ""):
    """Registra una acción de administrador"""
    try:
        async with cookie_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_audit_log (admin_id, action_type, target_id, details, timestamp) VALUES ($1, $2, $3, $4, NOW())",
                admin_id, action_type, target_id, details
            )
    except Exception as e:
        print(f"⚠️ Error registrando auditoría: {e}")

async def get_admin_audit_log(limit: int = 100):
    """Obtiene los últimos registros de auditoría"""
    async with cookie_db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM admin_audit_log ORDER BY timestamp DESC LIMIT $1",
            limit
        )
        return [dict(row) for row in rows]
# === FIN FUNCIONES AUDITORÍA ADMIN ===


# === INICIO FUNCIÓN EXPORTAR CSV ===
async def export_audit_log_to_csv(filename: str = None):
    """Exporta todos los movimientos de auditoría a un archivo CSV"""
    if not filename:
        filename = f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    async with cookie_db_pool.acquire() as conn:
        # Obtener TODOS los registros ordenados por fecha
        rows = await conn.fetch(
            "SELECT * FROM admin_audit_log ORDER BY timestamp ASC"
        )
        
        if not rows:
            return None, "No hay registros para exportar"
        
        # Crear archivo CSV
        import csv
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Escribir encabezados
            writer.writerow([
                'Fecha/Hora', 
                'Admin ID', 
                'Acción', 
                'Usuario Target', 
                'Detalles',
                'ID Registro'
            ])
            
            # Escribir datos
            for row in rows:
                writer.writerow([
                    row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                    row['admin_id'],
                    row['action_type'],
                    row['target_id'] if row['target_id'] else 'N/A',
                    row['details'] if row['details'] else 'Sin detalles',
                    str(row['id'])
                ])
        
        return filename, f"Exportados {len(rows)} registros"

async def get_audit_stats():
    """Obtiene estadísticas de la tabla de auditoría"""
    async with cookie_db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM admin_audit_log")
        admins = await conn.fetch("SELECT admin_id, COUNT(*) as cantidad FROM admin_audit_log GROUP BY admin_id ORDER BY cantidad DESC")
        return total, admins
# === FIN FUNCIÓN EXPORTAR CSV ===


def load_superadmins_from_db():
    """Carga los superadmins desde la base de datos"""
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM superadmins")
        superadmins = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return superadmins
    except Exception as e:
        print(f"⚠️ Error cargando superadmins: {e}")
        return [5531198491]  # Fallback al creador

def add_superadmin_to_db(user_id: int, added_by: int, is_creator: bool = False):
    """Agrega un superadmin a la base de datos"""
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        if key_manager.postgres_url:
            cursor.execute('''
                INSERT INTO superadmins (user_id, added_by, is_creator)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    added_by = EXCLUDED.added_by,
                    added_at = CURRENT_TIMESTAMP
            ''', (user_id, added_by, is_creator))
        else:
            cursor.execute('''
                INSERT OR REPLACE INTO superadmins (user_id, added_by, added_at, is_creator)
                VALUES (?, ?, datetime('now'), ?)
            ''', (user_id, added_by, 1 if is_creator else 0))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error agregando superadmin: {e}")
        return False

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Solo Superadmins pueden quitar admins"""
    try:
        user_id = update.effective_user.id
        
        if not is_superadmin(user_id):
            await update.message.reply_text("❌ Solo los Superadmins pueden quitar administradores.")
            return
        
        if len(context.args) < 1:
            await update.message.reply_text("❌ Formato: `/removeadmin <user_id>`")
            return
        
        target_id = int(context.args[0])
        
        # No puede quitarse a sí mismo
        if target_id == user_id:
            await update.message.reply_text("❌ No puedes quitarte a ti mismo.")
            return
        
        # Verificar si intenta quitar al creador
        if is_creator(target_id):
            await update.message.reply_text("❌ No se puede quitar al Creador del bot.")
            return
        
        # Si es superadmin (pero no creador), quitar de superadmins primero
        if is_superadmin(target_id):
            success, msg = remove_superadmin_from_db(target_id)
            if success:
                if target_id in SUPERADMINS:
                    SUPERADMINS.remove(target_id)
                await update.message.reply_text(
                    f"✅ Usuario `{target_id}` removido como Superadmin.\n"
                    f"📝 También se ha removido como Admin normal."
                )
            else:
                await update.message.reply_text(f"❌ {msg}")
            return
        
        # Si solo es admin normal, quitar de admins
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        if key_manager.postgres_url:
            cursor.execute("DELETE FROM admins WHERE user_id = %s", (target_id,))
        else:
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (target_id,))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ Usuario `{target_id}` ha sido removido como administrador."
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


def remove_superadmin_from_db(user_id: int):
    """Quita un superadmin de la base de datos"""
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        # No permitir quitar al creador principal
        cursor.execute("SELECT is_creator FROM superadmins WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result and result[0]:  # Es el creador
            conn.close()
            return False, "No se puede quitar al creador principal"
        
        if key_manager.postgres_url:
            cursor.execute("DELETE FROM superadmins WHERE user_id = %s", (user_id,))
        else:
            cursor.execute("DELETE FROM superadmins WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return True, "Superadmin removido"
    except Exception as e:
        return False, str(e)

# ============ VARIABLE GLOBAL ACTUALIZADA ============
# Inicializar vacío, se cargará desde BD en init_default_admin
SUPERADMINS = []

def is_superadmin(user_id: int) -> bool:
    """Verifica si el usuario es Superadmin (ahora consulta la BD)"""
    # Verificar en la lista en memoria (más rápido)
    if user_id in SUPERADMINS:
        return True
    
    # Verificar en base de datos por si acaso
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        if key_manager.postgres_url:
            cursor.execute("SELECT 1 FROM superadmins WHERE user_id = %s", (user_id,))
        else:
            cursor.execute("SELECT 1 FROM superadmins WHERE user_id = ?", (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            # Agregar a la lista en memoria para futuras consultas
            if user_id not in SUPERADMINS:
                SUPERADMINS.append(user_id)
            return True
        return False
    except Exception as e:
        print(f"⚠️ Error verificando superadmin: {e}")
        return user_id == 5531198491  # Fallback al creador

def is_creator(user_id: int) -> bool:
    """Verifica si el usuario es el creador principal"""
    try:
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        if key_manager.postgres_url:
            cursor.execute(
                "SELECT is_creator FROM superadmins WHERE user_id = %s", 
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT is_creator FROM superadmins WHERE user_id = ?", 
                (user_id,)
            )
        
        result = cursor.fetchone()
        conn.close()
        
        return result and result[0]
    except:
        return (user_id == SUPERADMINS[0]) if SUPERADMINS else (user_id == 5531198491)
    


# === INICIO CONFIGURACIÓN COOKIES ===
# Configuración del sistema de cookies (usa la misma DB de Railway)
COOKIE_API_URL = "https://pogo-procurer-exclude.ngrok-free.dev/api/v1"  # ⚠️ CAMBIA ESTA URL
COOKIE_BOT_API_KEY = "hades_4857f34ffeb549c1"      # ⚠️ CAMBIA ESTA KEY
COOKIE_CREDITS_PER_COOKIE = 10
COOKIE_MAX_CONCURRENT = 5
COOKIE_SUPER_ADMIN = 5531198491  # Tu ID

# Estado global para cookies
cookie_db_pool = None
cookie_active_sessions = {}
cookie_waiting_queue = []
cookie_sessions_lock = asyncio.Lock()
# === FIN CONFIGURACIÓN COOKIES ===


async def get_cookie_transactions(user_id: int, limit: int = 20):
    """Obtiene las últimas transacciones de un usuario"""
    async with cookie_db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, admin_id, amount, type, date 
            FROM cookie_transactions 
            WHERE user_id = $1 
            ORDER BY date DESC 
            LIMIT $2
            """,
            user_id, limit
        )
        return [dict(row) for row in rows]
    
    
    
    
async def usocreditos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el historial de uso de créditos del usuario"""
    user_id = update.effective_user.id
    
    try:
        # Obtener transacciones (últimas 15)
        transactions = await get_cookie_transactions(user_id, limit=15)
        
        if not transactions:
            await update.message.reply_text(
                "📋 *Historial de Créditos*\n\n"
                "No tienes transacciones registradas.\n\n"
                f"💰 Créditos actuales: {(await get_cookie_user(user_id))['credits']}",
                parse_mode='Markdown'
            )
            return
        
        # Construir mensaje
        text = "💳 *HISTORIAL DE CRÉDITOS*\n"
        text += f"👤 Usuario: `{user_id}`\n"
        text += "─" * 25 + "\n\n"
        
        for t in transactions:
            date_str = t['date'].strftime('%d/%m/%Y %H:%M') if isinstance(t['date'], datetime) else str(t['date'])[:16]
            
            # Icono según tipo
            if t['type'] == 'add':
                icon = "➕"
                sign = "+"
                color = "🟢"
            elif t['type'] == 'use':
                icon = "➖"
                sign = "-"
                color = "🔴"
            elif t['type'] == 'refund':
                icon = "↩️"
                sign = "+"
                color = "🟡"
            else:
                icon = "📝"
                sign = ""
                color = "⚪"
            
            # Quién hizo la transacción
            admin_info = ""
            if t['admin_id']:
                if t['admin_id'] == 'system':
                    admin_info = "🤖 Sistema"
                else:
                    admin_info = f"👤 Admin: `{t['admin_id']}`"
            else:
                admin_info = "👤 Tú"
            
            text += (
                f"{icon} *{date_str}*\n"
                f"{color} {sign}{t['amount']} créditos (`{t['type']}`)\n"
                f"📝 {admin_info}\n"
                f"└ ID: `{str(t['id'])[:8]}...`\n\n"
            )
        
        # Agregar resumen
        total_add = sum(t['amount'] for t in transactions if t['type'] == 'add')
        total_use = sum(t['amount'] for t in transactions if t['type'] == 'use')
        total_refund = sum(t['amount'] for t in transactions if t['type'] == 'refund')
        
        current_credits = (await get_cookie_user(user_id))['credits']
        
        text += "─" * 25 + "\n"
        text += f"📊 *Resumen (últimas {len(transactions)} transacciones)*\n"
        text += f"🟢 Recargados: +{total_add}\n"
        text += f"🔴 Usados: -{total_use}\n"
        text += f"🟡 Reembolsados: +{total_refund}\n"
        text += f"💰 *Saldo actual: {current_credits}*\n"
        text += "─" * 25
        
        # Agregar botón de volver al menú
        keyboard = [[InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]]
        
        await update.message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error al obtener historial: {e}")



async def export_cookie_transactions_to_csv(filename: str = None, user_id_filter: int = None):
    """Exporta transacciones de cookies a CSV"""
    if not filename:
        filename = f"cookie_transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    async with cookie_db_pool.acquire() as conn:
        # Query con o sin filtro
        if user_id_filter:
            rows = await conn.fetch(
                """
                SELECT t.*, u.username, u.first_name 
                FROM cookie_transactions t
                LEFT JOIN cookie_users u ON t.user_id = u.user_id
                WHERE t.user_id = $1
                ORDER BY t.date DESC
                """,
                user_id_filter
            )
        else:
            rows = await conn.fetch(
                """
                SELECT t.*, u.username, u.first_name 
                FROM cookie_transactions t
                LEFT JOIN cookie_users u ON t.user_id = u.user_id
                ORDER BY t.date DESC
                """
            )
        
        if not rows:
            return None, "No hay transacciones para exportar"
        
        # Crear CSV
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Encabezados
            writer.writerow([
                'Fecha/Hora', 'ID Transacción', 'User ID', 'Username', 
                'Nombre', 'Tipo', 'Cantidad', 'Admin ID', 'Detalles'
            ])
            
            for row in rows:
                writer.writerow([
                    row['date'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['date'], datetime) else str(row['date']),
                    str(row['id']),
                    row['user_id'],
                    row['username'] or 'N/A',
                    row['first_name'] or 'N/A',
                    row['type'],
                    row['amount'],
                    row['admin_id'] or 'N/A',
                    f"Transacción {row['type']}"
                ])
        
        return filename, f"Exportadas {len(rows)} transacciones"
    


async def export_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exporta todas las transacciones de créditos a CSV - Solo admins"""
    user_id = update.effective_user.id
    
    # Verificar si es admin
    if not await is_cookie_admin(user_id):
        await update.message.reply_text("❌ Solo administradores pueden usar este comando.")
        return
    
    # Verificar si hay argumento (filtrar por usuario específico)
    target_user = None
    if context.args:
        try:
            target_user = int(context.args[0])
            await update.message.reply_text(f"⏳ Exportando transacciones del usuario {target_user}...")
        except ValueError:
            await update.message.reply_text("❌ El ID de usuario debe ser un número.")
            return
    else:
        await update.message.reply_text("⏳ Exportando todas las transacciones de créditos...")
    
    try:
        # Generar archivo
        filename = f"cookie_transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath, message = await export_cookie_transactions_to_csv(filename, target_user)
        
        if not filepath:
            await update.message.reply_text(f"❌ {message}")
            return
        
        # Obtener estadísticas
        async with cookie_db_pool.acquire() as conn:
            if target_user:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM cookie_transactions WHERE user_id = $1",
                    target_user
                )
                total_credits = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount), 0) FROM cookie_transactions WHERE user_id = $1 AND type = 'add'",
                    target_user
                )
                used_credits = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount), 0) FROM cookie_transactions WHERE user_id = $1 AND type = 'use'",
                    target_user
                )
            else:
                total = await conn.fetchval("SELECT COUNT(*) FROM cookie_transactions")
                total_credits = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount), 0) FROM cookie_transactions WHERE type = 'add'"
                )
                used_credits = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount), 0) FROM cookie_transactions WHERE type = 'use'"
                )
        
        # Enviar archivo
        caption = (
            f"📊 *Exportación de Transacciones*\n\n"
            f"{'👤 Usuario: ' + str(target_user) + chr(10) if target_user else ''}"
            f"📝 Total transacciones: `{total}`\n"
            f"🟢 Créditos agregados: `{total_credits}`\n"
            f"🔴 Créditos usados: `{used_credits}`\n"
            f"📁 Archivo: `{filename}`"
        )
        
        await update.message.reply_document(
            document=open(filepath, 'rb'),
            caption=caption,
            parse_mode='Markdown'
        )
        
        # Limpiar archivo temporal
        import os
        os.remove(filepath)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error exportando: {e}")
        import traceback
        print(traceback.format_exc())



async def listcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista TODOS los usuarios con créditos - Solo admins"""
    user_id = update.effective_user.id
    
    if not await is_cookie_admin(user_id):
        await update.message.reply_text("❌ Solo administradores pueden usar este comando.")
        return
    
    try:
        async with cookie_db_pool.acquire() as conn:
            # Obtener TODOS los usuarios que tienen créditos O transacciones
            rows = await conn.fetch(
                """
                SELECT DISTINCT 
                    COALESCE(cu.user_id, ct.user_id) as user_id,
                    COALESCE(cu.username, '') as username,
                    COALESCE(cu.first_name, '') as first_name,
                    COALESCE(cu.credits, 0) as credits,
                    COALESCE((SELECT SUM(amount) FROM cookie_transactions 
                              WHERE user_id = COALESCE(cu.user_id, ct.user_id) 
                              AND type = 'add'), 0) as total_added,
                    COALESCE((SELECT SUM(amount) FROM cookie_transactions 
                              WHERE user_id = COALESCE(cu.user_id, ct.user_id) 
                              AND type = 'use'), 0) as total_used
                FROM cookie_users cu
                FULL OUTER JOIN cookie_transactions ct ON cu.user_id = ct.user_id
                WHERE cu.credits > 0 OR ct.user_id IS NOT NULL
                ORDER BY credits DESC, user_id ASC
                """
            )
        
        if not rows:
            await update.message.reply_text("📋 No hay usuarios con créditos.")
            return
        
        # Construir lista
        text = "👥 *LISTADO COMPLETO DE USUARIOS Y CRÉDITOS*\n\n"
        text += "`Username | UserID | Créditos Actuales | Agregados | Usados`\n"
        text += "─" * 50 + "\n\n"
        
        total_users = 0
        for row in rows:
            username = row['username'] or row['first_name'] or "SinNombre"
            user_id_db = row['user_id']
            credits = row['credits']
            added = row['total_added']
            used = row['total_used']
            
            # Escapar caracteres especiales
            username_clean = username.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
            
            text += f"• `{username_clean}` | `{user_id_db}` | `{credits}` | `{added}` | `{used}`\n"
            total_users += 1
        
        text += f"\n📊 Total: `{total_users}` usuarios con créditos/transacciones"
        
        # Enviar en partes si es muy largo
        if len(text) > 4000:
            parts = []
            current_part = "👥 *LISTADO COMPLETO DE USUARIOS Y CRÉDITOS*\n\n"
            
            for row in rows:
                username = row['username'] or row['first_name'] or "SinNombre"
                user_id_db = row['user_id']
                credits = row['credits']
                added = row['total_added']
                used = row['total_used']
                
                username_clean = username.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
                line = f"• `{username_clean}` | `{user_id_db}` | `{credits}` | `{added}` | `{used}`\n"
                
                if len(current_part) + len(line) > 4000:
                    parts.append(current_part)
                    current_part = "👥 *CONTINUACIÓN...*\n\n" + line
                else:
                    current_part += line
            
            parts.append(current_part)
            
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    part += f"\n📊 Total: `{total_users}` usuarios"
                await update.message.reply_text(part, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")



async def exportallcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exporta TODOS los usuarios con créditos a CSV - Solo admins"""
    user_id = update.effective_user.id
    
    if not await is_cookie_admin(user_id):
        await update.message.reply_text("❌ Solo administradores.")
        return
    
    await update.message.reply_text("⏳ Generando CSV...")
    
    try:
        filename = f"creditos_completos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        async with cookie_db_pool.acquire() as conn:
            # Primero: usuarios con créditos > 0
            rows_users = await conn.fetch(
                """
                SELECT user_id, username, first_name, credits, created_date, last_used
                FROM cookie_users 
                WHERE credits > 0
                """
            )
            
            # Segundo: usuarios con transacciones pero sin créditos (o no en tabla)
            rows_trans = await conn.fetch(
                """
                SELECT DISTINCT ct.user_id, 
                       COALESCE(cu.username, '') as username,
                       COALESCE(cu.first_name, '') as first_name,
                       COALESCE(cu.credits, 0) as credits,
                       COALESCE(cu.created_date, NOW()) as created_date,
                       COALESCE(cu.last_used, NOW()) as last_used
                FROM cookie_transactions ct
                LEFT JOIN cookie_users cu ON ct.user_id = cu.user_id
                WHERE ct.user_id NOT IN (SELECT user_id FROM cookie_users WHERE credits > 0)
                """
            )
            
            # Combinar
            all_users = list(rows_users) + list(rows_trans)
            
            if not all_users:
                await update.message.reply_text("📋 No hay usuarios.")
                return
            
            # Crear CSV
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'User ID', 'Username', 'Nombre', 'Créditos Actuales', 
                    'Total Agregado', 'Total Usado', 'Fecha Creación', 'Último Uso'
                ])
                
                for row in all_users:
                    # Calcular totales de transacciones
                    trans = await conn.fetchrow(
                        """
                        SELECT 
                            COALESCE(SUM(CASE WHEN type = 'add' THEN amount ELSE 0 END), 0) as added,
                            COALESCE(SUM(CASE WHEN type = 'use' THEN amount ELSE 0 END), 0) as used
                        FROM cookie_transactions 
                        WHERE user_id = $1
                        """, row['user_id']
                    )
                    
                    created_str = row['created_date'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['created_date'], datetime) else str(row['created_date'])
                    last_used_str = row['last_used'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['last_used'], datetime) else str(row['last_used'])
                    
                    writer.writerow([
                        row['user_id'],
                        row['username'] or '',
                        row['first_name'] or '',
                        row['credits'],
                        trans['added'] if trans else 0,
                        trans['used'] if trans else 0,
                        created_str,
                        last_used_str
                    ])
        
        total = len(all_users)
        await update.message.reply_document(
            document=open(filename, 'rb'),
            caption=f"📊 Exportación completa: `{total}` usuarios",
            parse_mode='Markdown'
        )
        
        import os
        os.remove(filename)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# --------------------------
# FUNCIONES DEL GENERADOR DE TARJETAS (integradas del código anterior)
# --------------------------

def _suma_luhn(numero_str: str, es_segundo_inicial: bool = False) -> int:
    if not numero_str.isdigit():
        raise ValueError(f"Solo dígitos: '{numero_str}'")
    suma    = 0
    segundo = es_segundo_inicial
    for c in reversed(numero_str):
        d = int(c)
        if segundo:
            d *= 2
            if d > 9:
                d -= 9
        suma   += d
        segundo = not segundo
    return suma


def es_valido_luhn(numero_str: str) -> bool:
    try:
        return _suma_luhn(numero_str, False) % 10 == 0
    except ValueError:
        return False


def calcular_digito_luhn(base: str) -> str:
    suma = _suma_luhn(base, True)
    return str((10 - (suma % 10)) % 10)


def detectar_red(numero: str) -> str:
    if not numero:
        return "Desconocida"

    if numero.startswith("4"):
        return "Visa"

    if len(numero) >= 2:
        d2 = int(numero[:2])
        if numero[:2] in ("34", "37"):
            return "Amex"
        if 51 <= d2 <= 55:
            return "Mastercard"
        if numero[:2] in ("36", "38"):
            return "Diners"
        if numero[:2] == "65":
            return "Discover"
        if numero[:2] in ("50", "56", "57", "58", "63", "67"):
            return "Maestro"

    if len(numero) >= 3:
        d3 = int(numero[:3])
        if 644 <= d3 <= 649:
            return "Discover"
        if numero[:3] in ("300", "301", "302", "303", "304", "305"):
            return "Diners"
        if numero[:3] == "220":
            return "Mir"

    if len(numero) >= 4:
        d4 = int(numero[:4])
        if 2221 <= d4 <= 2720:
            return "Mastercard"
        if numero[:4] == "6011":
            return "Discover"
        if numero[:4] in ("5018", "5020", "5038", "6304"):
            return "Maestro"
        if 3528 <= d4 <= 3589:
            return "JCB"
        if numero[:4] in ("2200", "2201", "2202", "2203", "2204"):
            return "Mir"

    if len(numero) >= 6:
        d6 = int(numero[:6])
        if 622126 <= d6 <= 622925:
            return "UnionPay"
        if 624000 <= d6 <= 626999:
            return "UnionPay"
        if 628200 <= d6 <= 628899:
            return "UnionPay"
        if 621693 <= d6 <= 621699:
            return "UnionPay"

    return "Otra"


_INFO_RED: dict = {
    "Visa":       {"longitudes": [13, 16], "cvv": 3},
    "Mastercard": {"longitudes": [16],     "cvv": 3},
    "Amex":       {"longitudes": [15],     "cvv": 4},
    "Diners":     {"longitudes": [14],     "cvv": 3},
    "Discover":   {"longitudes": [16],     "cvv": 3},
    "JCB":        {"longitudes": [16],     "cvv": 3},
    "Maestro":    {"longitudes": [13, 15, 16, 18, 19], "cvv": 3},
    "UnionPay":   {"longitudes": [16, 17, 18, 19],     "cvv": 3},
    "Mir":        {"longitudes": [16],     "cvv": 3},
    "Otra":       {"longitudes": [16],     "cvv": 3},
}


def info_red(bin_o_numero: str) -> dict:
    red   = detectar_red(bin_o_numero)
    datos = _INFO_RED.get(red, _INFO_RED["Otra"])
    return {
        "red":        red,
        "longitud":   datos["longitudes"][0],
        "cvv_len":    datos["cvv"],
        "longitudes": datos["longitudes"],
    }


def parsear_template(template: str) -> dict:
    partes     = [p.strip() for p in template.strip().split("|")]
    numero_tpl = partes[0].lower() if len(partes) > 0 else ""
    mes_tpl    = partes[1]         if len(partes) > 1 else "xx"
    anio_tpl   = partes[2]         if len(partes) > 2 else "20xx"
    cvv_tpl    = partes[3]         if len(partes) > 3 else "xxx"

    prefijo_fijo = ""
    for c in numero_tpl:
        if c == "x":
            break
        prefijo_fijo += c

    x_pattern = numero_tpl[len(prefijo_fijo):]
    longitud  = len(numero_tpl)

    return {
        "numero_tpl":   numero_tpl,
        "prefijo_fijo": prefijo_fijo,
        "x_pattern":    x_pattern,
        "mes_tpl":      mes_tpl,
        "anio_tpl":     anio_tpl,
        "cvv_tpl":      cvv_tpl,
        "longitud_num": longitud,
    }


def _rellenar_parte(patron: str) -> str:
    return "".join(
        str(random.randint(0, 9)) if c.lower() == "x" else c
        for c in patron
    )


def _mes_valido(mes: str) -> str:
    try:
        m = int(mes)
        return str(m).zfill(2) if 1 <= m <= 12 else str(random.randint(1, 12)).zfill(2)
    except ValueError:
        return str(random.randint(1, 12)).zfill(2)


def _anio_valido(anio: str) -> str:
    try:
        actual = datetime.now().year
        a      = int(anio)
        if a < 100:
            a = 2000 + a
        return str(a) if a >= actual else str(random.randint(actual, actual + 7))
    except ValueError:
        return str(random.randint(datetime.now().year, datetime.now().year + 7))


def generar_desde_template(p: dict, max_intentos: int = 50) -> dict:
    numero_tpl  = p["numero_tpl"]
    ultimo_es_x = numero_tpl[-1] == "x"
    numero      = ""

    for _ in range(max_intentos):
        if ultimo_es_x:
            base   = _rellenar_parte(numero_tpl[:-1])
            numero = base + calcular_digito_luhn(base)
            break
        else:
            numero = _rellenar_parte(numero_tpl)
        if es_valido_luhn(numero):
            break
    else:
        pos_ultima_x = numero_tpl.rfind("x")
        encontrado   = False
        if pos_ultima_x != -1:
            for _ in range(max_intentos):
                pre = _rellenar_parte(numero_tpl[:pos_ultima_x])
                suf = numero_tpl[pos_ultima_x + 1:]
                for d in range(10):
                    cand = pre + str(d) + suf
                    if es_valido_luhn(cand):
                        numero = cand
                        encontrado = True
                        break
                if encontrado:
                    break
        if not es_valido_luhn(numero):
            base   = _rellenar_parte(numero_tpl[:-1])
            numero = base + calcular_digito_luhn(base)

    mes  = _mes_valido(_rellenar_parte(p["mes_tpl"]))
    anio = _anio_valido(_rellenar_parte(p["anio_tpl"]))
    cvv  = _rellenar_parte(p["cvv_tpl"])

    return {
        "numero": numero,
        "mes":    mes,
        "anio":   anio,
        "cvv":    cvv,
        "valida": es_valido_luhn(numero),
        "pipe":   f"{numero}|{mes}|{anio}|{cvv}",
    }


def _delta_inteligente() -> int:
    while True:
        d = int(random.gauss(0, 15))
        if d != 0 and -300 <= d <= 300:
            return d


def _delta_variado() -> int:
    while True:
        d = int(random.gauss(0, 50))
        if d != 0 and -500 <= d <= 500:
            return d


def extrapolar_templates(
    template_base: str,
    cantidad: int = 10,
    modo: str = "inteligente",
    bin_len: int = 8,
) -> list:
    template_base = template_base.strip().lower()
    p            = parsear_template(template_base)
    prefijo_fijo = p["prefijo_fijo"]

    if not prefijo_fijo or not prefijo_fijo.isdigit():
        raise ValueError("El BIN necesita dígitos fijos antes de las x")

    if len(prefijo_fijo) <= bin_len:
        if len(prefijo_fijo) == bin_len:
            pass
        bin_parte    = prefijo_fijo[:-2] if len(prefijo_fijo) > 2 else ""
        cuenta_parte = prefijo_fijo[-2:] if len(prefijo_fijo) > 2 else prefijo_fijo
    else:
        bin_parte    = prefijo_fijo[:bin_len]
        cuenta_parte = prefijo_fijo[bin_len:]

    longitud_cuenta = len(cuenta_parte)
    cuenta_int      = int(cuenta_parte)
    cuenta_max      = int("9" * longitud_cuenta)

    max_posibles = cuenta_max if cuenta_max > 0 else 1
    if longitud_cuenta < 3:
        cantidad = min(cantidad, max_posibles)

    templates: set = {template_base}
    intentos       = 0
    espacio  = cuenta_max + 1
    factor   = max(50, min(200, espacio // max(1, cantidad)))
    max_iter = cantidad * factor

    while len(templates) - 1 < cantidad and intentos < max_iter:
        intentos += 1
        delta        = _delta_inteligente() if modo == "inteligente" else _delta_variado()
        nueva_int    = max(0, min(cuenta_max, cuenta_int + delta))
        nueva_cuenta = str(nueva_int).zfill(longitud_cuenta)
        nuevo        = (
            f"{bin_parte}{nueva_cuenta}"
            f"{p['x_pattern']}|{p['mes_tpl']}|{p['anio_tpl']}|{p['cvv_tpl']}"
        )
        if nuevo not in templates:
            templates.add(nuevo)

        if longitud_cuenta < 3 and len(templates) - 1 >= max_posibles:
            break

    resultado = [t for t in templates if t != template_base]
    return sorted(resultado)[:cantidad]


def _extraer_numero(linea: str) -> str:
    return linea.strip().split("|")[0].strip()


def analizar_patron(
    entradas: list,
    umbral: float = 0.6,
    mes_tpl: str = "xx",
    anio_tpl: str = "20xx",
    cvv_tpl: str = "xxx",
) -> tuple:
    numeros = [_extraer_numero(e) for e in entradas if _extraer_numero(e).isdigit()]
    if len(numeros) < 2:
        raise ValueError("Se necesitan al menos 2 números válidos")

    longitud = Counter(len(n) for n in numeros).most_common(1)[0][0]
    total_iniciales = len(numeros)
    numeros  = [n for n in numeros if len(n) == longitud]

    descartadas = total_iniciales - len(numeros)

    template = ""
    reporte  = {}

    for pos in range(longitud):
        digitos_pos     = [n[pos] for n in numeros]
        conteo          = Counter(digitos_pos)
        mas_comun, freq = conteo.most_common(1)[0]
        porcentaje      = freq / len(numeros)

        reporte[pos + 1] = {
            "digito_top": mas_comun,
            "frecuencia": freq,
            "total":      len(numeros),
            "porcentaje": round(porcentaje * 100, 1),
            "fijo":       porcentaje > umbral,
        }
        template += mas_comun if porcentaje > umbral else "x"

    bin_confiable = ""
    for c in template:
        if c == "x":
            break
        bin_confiable += c

    return f"{template}|{mes_tpl}|{anio_tpl}|{cvv_tpl}", reporte, bin_confiable


def generar_expiracion() -> tuple:
    anio = datetime.now().year
    return str(random.randint(1, 12)).zfill(2), str(random.randint(anio, anio + 7))


def generar_cvv(longitud: int = 3) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(longitud))


def construir_template_desde_bin(
    bin_str:       str,
    cuenta_parcial: str = "",
    mes_fijo:      str = "xx",
    anio_fijo:     str = "20xx",
    cvv_fijo:      str = "",
    longitud:      int = None,
) -> str:
    bin_str = bin_str.strip()
    if not bin_str.isdigit():
        raise ValueError(f"BIN inválido: '{bin_str}'")
    d = info_red(bin_str)
    if longitud is None:
        longitud = d["longitud"]
    cvv_len = d["cvv_len"]
    cvv_tpl = cvv_fijo if cvv_fijo else "x" * cvv_len
    prefijo = bin_str + cuenta_parcial
    if len(prefijo) >= longitud:
        raise ValueError(
            f"Prefijo ({len(prefijo)} dígitos) >= longitud de la tarjeta ({longitud})"
        )
    x_count    = longitud - len(prefijo)
    numero_tpl = prefijo + "x" * x_count
    return f"{numero_tpl}|{mes_fijo}|{anio_fijo}|{cvv_tpl}"


# --------------------------
# CONFIGURACIÓN DEL BOT DE TELEGRAM
# --------------------------

# Estados para la conversación del generador de tarjetas
ESTADO_MENU = 0
ESTADO_EXTRAPOLAR = 1
ESTADO_LIVES = 2
ESTADO_BIN = 3
ESTADO_MODO = 4
ESTADO_BIN_LEN = 5
ESTADO_CANTIDAD = 6

def get_card_info(bin_number: str):
    try:
        with open('tarjetas.csv', mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                # Compara sin importar mayúsculas/minúsculas
                if row['bin'].strip() == bin_number:
                    return {
                        'Marca': row['brand'],
                        'Tipo de tarjeta': row['tipo'],
                        'Nivel de tarjeta': row['nivel'],
                        'Banco': row['Banco'],
                        'Teléfono': row['teléfono'],
                        'País': row['país']
                    }
        return None
    except FileNotFoundError:
        print("El archivo tarjetas.csv no fue encontrado.")
        return None
    except Exception as e:
        print(f"Error al leer el archivo CSV: {e}")
        return None
    

# Función para administradores: exportar usuarios y admins a un archivo TXT
async def export_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exportar usuarios y admins - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        # Verificar si es SUPERADMIN
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden exportar datos.\n\n"
                "⚠️ Esta función exporta información sensible del sistema.",
                parse_mode='Markdown'
            )
            return
        
        # Usar la conexión del KeyManager (PostgreSQL o SQLite)
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        # Obtener admins
        cursor.execute("SELECT user_id FROM admins")
        admins = [row[0] for row in cursor.fetchall()]
        
        # Obtener usuarios CON plan incluido
        cursor.execute('''
        SELECT user_id, key, plan, activated_at, expires_at 
        FROM active_keys
        ''')
        users_data = cursor.fetchall()
        
        # Obtener superadmins
        cursor.execute("SELECT user_id, is_creator FROM superadmins")
        superadmins_data = cursor.fetchall()
        
        conn.close()
        
        # Generar contenido del archivo
        content = "EXPORTACIÓN DE DATOS - HADES V1\n"
        content += "=" * 50 + "\n\n"
        content += f"Fecha de exportación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # SUPERADMINS
        content += "SUPERADMINS:\n"
        content += "-" * 20 + "\n"
        if superadmins_data:
            for sa_id, is_creator in superadmins_data:
                role = "👑 Creador" if is_creator else "⭐ Superadmin"
                content += f"ID: {sa_id} | Rol: {role}\n"
        else:
            content += "No hay superadmins registrados.\n"
        
        content += "\n"
        
        # Administradores
        content += "ADMINISTRADORES:\n"
        content += "-" * 20 + "\n"
        if admins:
            for admin_id in admins:
                content += f"ID: {admin_id}\n"
        else:
            content += "No hay administradores.\n"
        
        content += "\n"
        
        # Usuarios con PLAN incluido
        content += "USUARIOS CON CLAVES ACTIVAS:\n"
        content += "-" * 30 + "\n"
        
        if users_data:
            for user_data in users_data:
                user_id_val, key, plan, activated_at, expires_at = user_data
                
                # Manejar tanto datetime objects (PostgreSQL) como strings (SQLite)
                if isinstance(expires_at, str):
                    expire_date = datetime.fromisoformat(expires_at)
                else:
                    expire_date = expires_at
                
                is_expired = datetime.now() > expire_date
                days_remaining = max(0, (expire_date - datetime.now()).days)
                
                if is_expired:
                    estado_str = "Expirada"
                else:
                    estado_str = f"Activa ({days_remaining} días)"
                
                content += f"ID: {user_id_val} | Clave: {key} | Estado: {estado_str} | Plan: {plan}\n"
        else:
            content += "No hay usuarios con claves activas.\n"
        
        # Guardar archivo temporal
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Enviar archivo
        await update.message.reply_text(
            f"✅ Exportación completada:\n"
            f"• {len(superadmins_data)} superadmins\n"
            f"• {len(admins)} administradores\n"
            f"• {len(users_data)} usuarios"
        )
        
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(filename, 'rb'),
            caption=f"Exportación Hades V1 - {datetime.now().strftime('%Y-%m-%d')}"
        )
        
        os.remove(filename)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        import traceback
        print(traceback.format_exc())

# Función para administradores: exportar solo usuarios a un archivo TXT
async def export_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exportar solo usuarios - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden exportar usuarios.",
                parse_mode='Markdown'
            )
            return
        
        # Obtener la lista de usuarios con claves activas
        users = key_manager.get_all_active_users()
        
        # Crear el contenido del archivo
        content = "EXPORTACIÓN DE USUARIOS - HADES V1\n"
        content += "=" * 50 + "\n\n"
        
        # Agregar fecha de exportación
        content += f"Fecha de exportación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Agregar usuarios con claves activas
        content += "USUARIOS CON CLAVES ACTIVAS:\n"
        content += "-" * 30 + "\n"
        if users:
            for user in users:
                status = "Expirada" if user["is_expired"] else f"Activa ({user['days_remaining']} días)"
                active_requests = user_request_count.get(user['user_id'], 0)
                content += f"ID: {user['user_id']} | Clave: {user['key']} | Estado: {status} | Peticiones: {active_requests}/{MAX_USER_REQUESTS}\n"
        else:
            content += "No hay usuarios con claves activas.\n"
        
        # Guardar en un archivo temporal
        filename = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Enviar el archivo al administrador
        await update.message.reply_text(f"✅ Exportación de usuarios completada. Enviando archivo: {filename}")
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(filename, 'rb'),
            caption=f"Exportación de usuarios de Hades V1 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Opcional: eliminar el archivo después de enviarlo
        import os
        os.remove(filename)
    
    except Exception as e:
        await update.message.reply_text(f"Error al exportar usuarios: {e}")

# Función para administradores: exportar solo administradores a un archivo TXT
async def export_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exportar solo administradores - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden exportar administradores.",
                parse_mode='Markdown'
            )
            return
        
        # Obtener la lista de administradores
        admins = key_manager.get_admins()
        
        # Obtener superadmins
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, is_creator FROM superadmins")
        superadmins_data = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        
        # Crear el contenido del archivo
        content = "EXPORTACIÓN DE ADMINISTRADORES - HADES V1\n"
        content += "=" * 50 + "\n\n"
        
        # Agregar fecha de exportación
        content += f"Fecha de exportación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Agregar administradores
        content += "ADMINISTRADORES:\n"
        content += "-" * 20 + "\n"
        if admins:
            for admin_id in admins:
                if admin_id in superadmins_data:
                    role = "👑 Creador" if superadmins_data[admin_id] else "⭐ Superadmin"
                else:
                    role = "🔵 Admin"
                content += f"ID: {admin_id} | Rol: {role}\n"
        else:
            content += "No hay administradores registrados.\n"
        
        # Guardar en un archivo temporal
        filename = f"admins_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Enviar el archivo al administrador
        await update.message.reply_text(f"✅ Exportación de administradores completada. Enviando archivo: {filename}")
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(filename, 'rb'),
            caption=f"Exportación de administradores de Hades V1 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Opcional: eliminar el archivo después de enviarlo
        import os
        os.remove(filename)
    
    except Exception as e:
        await update.message.reply_text(f"Error al exportar administradores: {e}")


# Decorador para verificar si el usuario tiene una clave activa
def require_key(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Los administradores siempre tienen acceso
        if key_manager.is_admin(user_id):
            return await func(update, context)
        
        # Verificar si el usuario tiene una clave activa
        if not key_manager.has_active_key(user_id):
            await update.message.reply_text(
                "❌ No tienes una clave activa.\n\n"
                "📲 Ingresa al grupo https://t.me/EternalTartaro\n"
                "y pregunta por el staff con /staff para obtener acceso.\n\n"
                "Una vez tengas tu key, usa /activar <clave> para activarla."
            )
            return
        
        # Control de tiempo entre peticiones - Solo proteger variables compartidas
        now = datetime.now()
        
        # Usar el lock solo para operaciones con diccionarios
        async with user_lock:
            if user_id in last_request_time:
                time_diff = (now - last_request_time[user_id]).total_seconds()
                if time_diff < MIN_TIME_BETWEEN_REQUESTS:
                    remaining_time = MIN_TIME_BETWEEN_REQUESTS - time_diff
                    await update.message.reply_text(
                        f"⏳ Debes esperar {remaining_time:.1f} segundos antes de hacer otra petición."
                    )
                    return
            
            # Verificar si el usuario ya está procesando una petición (solo evitar spam del mismo usuario)
            if user_id in user_processing and user_processing[user_id]:
                await update.message.reply_text(
                    "⏳ Ya tienes una petición en proceso. Por favor, espera a que termine antes de hacer otra."
                )
                return
            
            # Actualizar el tiempo de la última petición y marcar como en proceso
            last_request_time[user_id] = now
            user_processing[user_id] = True
        
        try:
            # Ejecutar la función SIN el lock (permite concurrencia entre usuarios)
            result = await func(update, context)
            return result
        finally:
            # Liberar el marcador de proceso
            async with user_lock:
                user_processing[user_id] = False
    
    return wrapped


def require_plan(planos_permitidos):
    """
    Decorador que verifica si el plan del usuario está en la lista de planes permitidos.
    Solo los usuarios con planes en la lista pueden usar el comando.
    
    Args:
        planos_permitidos: Lista de nombres de planes que pueden usar el comando
                          Ej: ['OLIMPO', '1MES', '15DIAS']
    """
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            
            # Los administradores siempre tienen acceso
            if key_manager.is_admin(user_id):
                return await func(update, context, *args, **kwargs)
            
            # Obtener el plan del usuario
            plan = key_manager.get_user_plan(user_id)
            
            if not plan:
                await update.message.reply_text(
                    "❌ No se pudo verificar tu plan de acceso.\n"
                    "Contacta a un administrador."
                )
                return
            
            # Verificar si el plan está en la lista de permitidos
            if plan not in planos_permitidos:
                # Obtener info del plan para mostrar restricciones
                plan_config = PLANES.get(plan, PLANES['1SEMA'])
                restricciones = plan_config['restricciones']
                
                mensaje = (
                    f"❌ *Acceso Denegado*\n\n"
                    f"Tu plan actual: *{plan}*\n"
                    f"Este comando requiere plan: {', '.join(planos_permitidos)}\n\n"
                )
                
                if restricciones:
                    mensaje += f"*Comandos bloqueados en tu plan:*\n"
                    mensaje += "\n".join([f"• /{cmd}" for cmd in restricciones])
                else:
                    mensaje += "*Tu plan tiene acceso total* excepto a este comando específico."
                
                await update.message.reply_text(mensaje, parse_mode='Markdown')
                return
            
            # Si el plan está permitido, ejecutar la función
            return await func(update, context, *args, **kwargs)
        
        return wrapper
    return decorator




# Función para activar una clave
async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Verificar si se proporcionó una clave
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/activar <clave>\n\n"
                "Ejemplo:\n"
                "/activar A1B2C3D4E5F6G7H8"
            )
            return
        
        # Extraer la clave
        key = context.args[0]
        user_id = update.effective_user.id
        
        # Activar la clave
        success, message = key_manager.activate_key(key, user_id)
        
        if success:
            await update.message.reply_text(f"✅ {message}\n\nAhora puedes usar todos los comandos del bot.")
        else:
            await update.message.reply_text(f"❌ {message}")
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para verificar el estado de la clave
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        status = key_manager.get_user_status(user_id)
        
        if not status:
            await update.message.reply_text(
                "❌ No tienes una clave activa.\n\n"
                "📲 Ingresa al grupo https://t.me/EternalTartaro\n"
                "y pregunta por el staff con /staff para obtener acceso.\n\n"
                "Una vez tengas tu key, usa /activar <clave> para activarla."
            )
            return
        
        # Obtener plan
        plan = status.get("plan", "1SEMA")
        plan_info = PLANES.get(plan, PLANES['1SEMA'])
        
        # Definir variables que faltaban
        activated_at = datetime.fromisoformat(status["activated_at"]).strftime("%Y-%m-%d %H:%M:%S")
        expires_at = datetime.fromisoformat(status["expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
        
        # Definir el estado (variable que faltaba)
        if status["is_expired"]:
            estado = "❌ Expirada"
        else:
            estado = f"✅ Activa ({status['days_remaining']} días restantes)"
        
        # Construir restricciones
        restricciones = ""
        if plan_info['restricciones']:
            restricciones = "\n❌ *Restricciones:*\n" + "\n".join([f"• /{r}" for r in plan_info['restricciones']])
        else:
            restricciones = "\n✅ *Acceso total (sin restricciones)*"
        
        response = (
            "----------------------------\n"
            "🏛️Hades V1🏛️ - Estado de Clave\n"
            "----------------------------\n"
            f"🔹 Usuario ID: {status['user_id']}\n"
            f"🔹 Plan: *{plan}*\n"
            f"🔹 Descripción: {plan_info['descripcion']}\n"
            f"🔹 Estado: {estado}\n"
            f"🔹 Activada: {activated_at}\n"
            f"🔹 Expira: {expires_at}\n"
            f"🔹 Días restantes: {status['days_remaining']}\n"
            f"{restricciones}\n"
            "----------------------------\n"
            f"🔹 Desarrollado por: @Chack0071"
        )
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para administradores: generar una clave
async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        if not key_manager.is_admin(user_id):
            await update.message.reply_text("❌ No tienes permisos.")
            return
        
        # Formato: /genkey <user_id> <plan> <dias>
        if len(context.args) < 3:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/genkey <user_id> <plan> <dias>\n\n"
                "Planes disponibles:\n"
                "• OLIMPO - Todo excepto admin\n"
                "• 1MES - Todo excepto admin\n" 
                "• 15DIAS - Sin /ph, /di\n"
                "• 1SEMA - Solo básicos + Amazon\n\n"
                "Ejemplo:\n"
                "/genkey 123456789 OLIMPO 30"
            )
            return
        
        target_user_id = int(context.args[0])
        plan = context.args[1].upper()
        days = int(context.args[2])
        
        # Validar plan
        if plan not in PLANES:
            await update.message.reply_text(
                f"❌ Plan inválido.\n"
                f"Opciones: {', '.join(PLANES.keys())}"
            )
            return
        
        key = key_manager.generate_key(target_user_id, plan, days, user_id)
        
        # REGISTRAR EN AUDITORÍA
        await log_admin_action(
            admin_id=user_id,
            action_type="GENKEY",
            target_id=target_user_id,
            details=f"Plan: {plan}, Días: {days}, Clave: {key}"
        )
        await update.message.reply_text(
            f"✅ Clave generada:\n\n"
            f"🔹 Usuario: {target_user_id}\n"
            f"🔹 Plan: {plan}\n"
            f"🔹 Días: {days}\n"
            f"🔹 Clave: `{key}`\n\n"
            f"El usuario debe activar con:\n"
            f"`/activar {key}`",
            parse_mode='Markdown'
        )
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        
        

# ============ CONFIGURACIÓN DE SUPERADMINS ============
# Lista de Superadmins (IDs de Telegram)
# Solo estos pueden agregar/quitar admins
SUPERADMINS = [5531198491]  # Tu ID como superadmin principal

def is_superadmin(user_id: int) -> bool:
    """Verifica si el usuario es Superadmin"""
    return user_id in SUPERADMINS or key_manager.is_admin(user_id) and user_id == SUPERADMINS[0]

def get_admin_level(user_id: int) -> str:
    """Retorna el nivel del admin: 'superadmin', 'admin', o None"""
    if user_id in SUPERADMINS:
        return "superadmin"
    elif key_manager.is_admin(user_id):
        return "admin"
    return None


# Función para administradores: agregar un nuevo administrador
# Función para administradores: agregar un nuevo administrador (SOLO SUPERADMINS)
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Verificar si es SUPERADMIN (no solo admin normal)
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden agregar nuevos administradores.\n"
                "Los admins normales no tienen este permiso.",
                parse_mode='Markdown'
            )
            return
        
        # Verificar si se proporcionó el ID del nuevo administrador
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/addadmin <user_id>\n\n"
                "Ejemplo:\n"
                "/addadmin 123456789\n\n"
                "⚠️ Nota: Solo Superadmins pueden usar este comando."
            )
            return
        
        new_admin_id = int(context.args[0])
        
        # Verificar si ya es administrador
        if key_manager.is_admin(new_admin_id):
            await update.message.reply_text(f"❌ El usuario {new_admin_id} ya es administrador.")
            return
        
        # Verificar si es superadmin (no se puede agregar como admin normal)
        if new_admin_id in SUPERADMINS:
            await update.message.reply_text(f"ℹ️ El usuario {new_admin_id} ya es Superadmin.")
            return
        
        # Agregar el nuevo administrador
        # REGISTRAR EN AUDITORÍA
        await log_admin_action(
                admin_id=user_id,
                action_type="ADD_ADMIN",
                target_id=new_admin_id,
                details="Agregó nuevo administrador"
            )    
        if key_manager.add_admin(new_admin_id, user_id):
            await update.message.reply_text(
                f"✅ *Administrador agregado correctamente*\n\n"
                f"🆔 ID: `{new_admin_id}`\n"
                f"👤 Agregado por: Superadmin `{user_id}`\n"
                f"🔰 Rol: *Admin Normal*\n\n"
                f"Este usuario ahora tiene acceso a comandos de administración "
                f"excepto agregar/quitar otros admins.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(f"❌ No se pudo agregar al administrador {new_admin_id}.")
    
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número entero.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para Superadmins: agregar un nuevo Superadmin (opcional)
async def add_superadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Solo el creador puede agregar otros superadmins"""
    try:
        user_id = update.effective_user.id
        
        # Verificar si es el creador principal
        if not is_creator(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo el *Creador del Bot* puede designar otros Superadmins.\n"
                "Los Superadmins normales no tienen este permiso.",
                parse_mode='Markdown'
            )
            return
        
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato: `/addsuperadmin <user_id>`\n\n"
                "⚠️ Advertencia: Los Superadmins tienen control total del bot.",
                parse_mode='Markdown'
            )
            return
        
        new_superadmin_id = int(context.args[0])
        
        # Verificar si ya es superadmin
        if is_superadmin(new_superadmin_id):
            await update.message.reply_text("❌ Este usuario ya es Superadmin.")
            return
        
        # Guardar en base de datos
        if add_superadmin_to_db(new_superadmin_id, user_id, is_creator=False):
            # Agregar a la lista en memoria
            SUPERADMINS.append(new_superadmin_id)
            
            # También agregar como admin normal si no lo es
            if not key_manager.is_admin(new_superadmin_id):
                key_manager.add_admin(new_superadmin_id, user_id)
            
            await update.message.reply_text(
                f"✅ *Nuevo Superadmin agregado*\n\n"
                f"🆔 ID: `{new_superadmin_id}`\n"
                f"👤 Agregado por: `{user_id}`\n"
                f"🔰 Rol: *Superadmin*\n\n"
                f"⚠️ Este usuario ahora tiene control total del bot.\n"
                f"✅ Guardado permanentemente en la base de datos.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error al guardar en la base de datos.")
        
    except ValueError:
        await update.message.reply_text("❌ El ID debe ser un número.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# Función mejorada para listar admins (muestra roles)
# Configuración de paginación para listados
ITEMS_PER_PAGE = 10

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista paginada de admins - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        # Verificar si es SUPERADMIN
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden ver la lista de administradores.",
                parse_mode='Markdown'
            )
            return
        
        # Obtener todos los admins
        admins = key_manager.get_admins()
        
        if not admins:
            await update.message.reply_text("👥 No hay administradores registrados.")
            return
        
        # Guardar en contexto para navegación
        context.user_data['admin_list'] = admins
        context.user_data['admin_page'] = 1
        
        # Mostrar primera página
        await show_admins_page(update, context, 1, is_callback=False)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def show_admins_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, is_callback: bool = True):
    """Muestra una página específica de admins"""
    admins = context.user_data.get('admin_list', [])
    total_items = len(admins)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items > 0 else 1
    
    # Validar página
    page = max(1, min(page, total_pages))
    context.user_data['admin_page'] = page
    
    # Calcular índices
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_admins = admins[start_idx:end_idx]
    
    # Construir mensaje
    text = (
        f"👑 *PANEL DE SUPERADMIN*\n"
        f"📋 Lista de Administradores\n\n"
        f"📄 Página {page} de {total_pages} "
        f"({start_idx + 1}-{min(end_idx, total_items)} de {total_items})\n"
        f"{'─' * 30}\n\n"
    )
    
    for admin_id in current_admins:
        if admin_id in SUPERADMINS:
            if admin_id == SUPERADMINS[0]:
                role = "👑 Creador"
            else:
                role = "⭐ Superadmin"
        else:
            role = "🔵 Admin"
        
        text += f"🆔 `{admin_id}`\n   └ {role}\n\n"
    
    # Crear botones de navegación
    keyboard = []
    nav_buttons = []
    
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Anterior", callback_data=f"adminlist:page:{page-1}")
        )
    
    nav_buttons.append(
        InlineKeyboardButton("🏠 Menú", callback_data="menu:admin")
    )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("➡️ Siguiente", callback_data=f"adminlist:page:{page+1}")
        )
    
    keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 Actualizar", callback_data="adminlist:refresh")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_callback:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )

async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista paginada de usuarios - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        # Verificar si es SUPERADMIN
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden ver la lista de usuarios.",
                parse_mode='Markdown'
            )
            return
        
        # Obtener todos los usuarios
        users = key_manager.get_all_active_users()
        
        if not users:
            await update.message.reply_text("👥 No hay usuarios con claves activas.")
            return
        
        # Guardar en contexto para navegación
        context.user_data['users_list'] = users
        context.user_data['users_page'] = 1
        
        # Mostrar primera página
        await show_users_page(update, context, 1, is_callback=False)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def show_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, is_callback: bool = True):
    """Muestra una página específica de usuarios"""
    users = context.user_data.get('users_list', [])
    total_items = len(users)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items > 0 else 1
    
    # Validar página
    page = max(1, min(page, total_pages))
    context.user_data['users_page'] = page
    
    # Calcular índices
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_users = users[start_idx:end_idx]
    
    # Construir mensaje
    text = (
        f"👑 *PANEL DE SUPERADMIN*\n"
        f"📋 Lista de Usuarios Activos\n\n"
        f"📄 Página {page} de {total_pages} "
        f"({start_idx + 1}-{min(end_idx, total_items)} de {total_items})\n"
        f"{'─' * 30}\n\n"
    )
    
    for user in current_users:
        user_id = user['user_id']
        plan = user.get('plan', 'N/A')
        
        if user['is_expired']:
            status = "❌ Expirada"
            emoji = "🔴"
        else:
            status = f"✅ {user['days_remaining']} días"
            emoji = "🟢"
        
        text += (
            f"{emoji} 🆔 `{user_id}`\n"
            f"   └ Plan: *{plan}* | {status}\n\n"
        )
    
    # Crear botones de navegación
    keyboard = []
    nav_buttons = []
    
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Anterior", callback_data=f"userlist:page:{page-1}")
        )
    
    nav_buttons.append(
        InlineKeyboardButton("🏠 Menú", callback_data="menu:admin")
    )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("➡️ Siguiente", callback_data=f"userlist:page:{page+1}")
        )
    
    keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 Actualizar", callback_data="userlist:refresh")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_callback:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )

# Handler para callbacks de navegación de listas
async def list_navigation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la navegación de listas paginadas"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    # Verificar superadmin
    if not is_superadmin(user_id):
        await query.answer("❌ No tienes permisos", show_alert=True)
        return
    
    data = query.data
    
    # Navegación de admins
    if data.startswith("adminlist:"):
        if data == "adminlist:refresh":
            # Recargar lista
            admins = key_manager.get_admins()
            context.user_data['admin_list'] = admins
            page = context.user_data.get('admin_page', 1)
            await show_admins_page(update, context, page)
        else:
            # Cambiar página
            page = int(data.split(":")[-1])
            await show_admins_page(update, context, page)
    
    # Navegación de usuarios
    elif data.startswith("userlist:"):
        if data == "userlist:refresh":
            # Recargar lista
            users = key_manager.get_all_active_users()
            context.user_data['users_list'] = users
            page = context.user_data.get('users_page', 1)
            await show_users_page(update, context, page)
        else:
            # Cambiar página
            page = int(data.split(":")[-1])
            await show_users_page(update, context, page)




# Función para administradores: revocar una clave
async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Verificar si es administrador
        if not key_manager.is_admin(user_id):
            await update.message.reply_text("❌ No tienes permisos para ejecutar este comando.")
            return
        
        # Verificar si se proporcionó el ID de usuario
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/revoke <user_id>\n\n"
                "Ejemplo:\n"
                "/revoke 123456789"
            )
            return
        
        target_user_id = int(context.args[0])
        # REGISTRAR EN AUDITORÍA
        await log_admin_action(
                    admin_id=user_id,
                    action_type="REVOKE_KEY",
                    target_id=target_user_id,
                    details="Revocó clave de usuario"
                )    
        # Revocar la clave
        if key_manager.revoke_key(target_user_id):
            await update.message.reply_text(f"✅ Clave del usuario {target_user_id} revocada correctamente.")
        else:
            await update.message.reply_text(f"❌ No se pudo revocar la clave del usuario {target_user_id} o no tenía clave activa.")
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para administradores: extender una clave
async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Verificar si es administrador
        if not key_manager.is_admin(user_id):
            await update.message.reply_text("❌ No tienes permisos para ejecutar este comando.")
            return
        
        # Verificar si se proporcionaron los argumentos necesarios
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/extend <user_id> <dias>\n\n"
                "Ejemplo:\n"
                "/extend 123456789 15"
            )
            return
        
        target_user_id = int(context.args[0])
        days = int(context.args[1])
        
        # Extender la clave
        success, message = key_manager.extend_key(target_user_id, days)
        
        if success:
            # REGISTRAR EN AUDITORÍA
            await log_admin_action(
                admin_id=user_id,
                action_type="EXTEND_KEY",
                target_id=target_user_id,
                details=f"Extendió {days} días"
            )
            await update.message.reply_text(f"✅ {message}")
        else:
            await update.message.reply_text(f"❌ {message}")
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para obtener el ID del usuario
async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Tu ID de usuario es: {update.effective_user.id}")

# Función de callback para enviar resultados de tarjetas en tiempo real
async def send_card_result(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, result: dict, card_data: str, user_id: int, original_update: Update):
    """
    Función de callback para enviar resultados de tarjetas en tiempo real
    """
    try:
        # Validar formato de la tarjeta
        card_parts = card_data.split("|")
        if len(card_parts) != 4:
            await original_update.message.reply_text(
                f"❌ Formato de tarjeta incorrecto en la tarjeta {index+1}: {card_data}"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Approved":
            status_icon = "✅ Approved"
            resultado = "APROBADA"
        elif status_raw == "❌ Declined":
            status_icon = "❌ Declined"
            resultado = "DECLINADA"
        else:
            status_icon = result["status"]
            resultado = "ERROR"
        
        # Construir mensaje de respuesta con el estilo de la imagen
        # Usando el formato <code> para que sea copiable
        
        # El enlace a la tarjeta (copiable con formato code)
        card_code = f"<code>{card_data}</code> <a href='tg://copy?text={card_data}'></a>"
        
        # El enlace al usuario (copiable con formato code)
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Mass Amazon Check\n"
            "🔥 Comando: /azmass\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 Desarrollado por:\n"
            f"🔥 {dev_code}\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Enviar el resultado con el botón
        sent_message = await original_update.message.reply_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Approved", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Approved":
            asyncio.create_task(delete_message_after_delay(context, sent_message.chat_id, sent_message.message_id, 30))
    
    except Exception as e:
        await original_update.message.reply_text(f"Error procesando tarjeta {index+1}: {e}")

# Función para eliminar un mensaje después de un tiempo determinado
async def delete_message_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay_seconds: int):
    """
    Elimina un mensaje después de un tiempo determinado
    """
    try:
        # Esperar el tiempo especificado
        await asyncio.sleep(delay_seconds)
        
        # Intentar eliminar el mensaje
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        # Ignorar errores al eliminar el mensaje (puede que ya haya sido eliminado)
        print(f"Error al eliminar mensaje: {e}")

async def export_cookie_users_to_csv(filename: str = None):
    """Exporta todos los usuarios del sistema de cookies a CSV"""
    if not filename:
        filename = f"cookie_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    async with cookie_db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, username, first_name, credits, created_date, last_used
            FROM cookie_users
            ORDER BY credits DESC, user_id ASC
            """
        )
        
        if not rows:
            return None, "No hay usuarios registrados en el sistema de cookies"
        
        # Crear CSV
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Encabezados
            writer.writerow([
                'User ID', 'Username', 'Nombre', 'Créditos', 
                'Fecha Creación', 'Último Uso', 'Estado'
            ])
            
            for row in rows:
                # Determinar estado según créditos
                if row['credits'] >= 1000:
                    estado = "VIP"
                elif row['credits'] >= 100:
                    estado = "Activo"
                elif row['credits'] > 0:
                    estado = "Bajo"
                else:
                    estado = "Sin créditos"
                
                writer.writerow([
                    row['user_id'],
                    row['username'] or '',
                    row['first_name'] or '',
                    row['credits'],
                    row['created_date'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['created_date'], datetime) else str(row['created_date']),
                    row['last_used'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['last_used'], datetime) else str(row['last_used']),
                    estado
                ])
        
        return filename, f"Exportados {len(rows)} usuarios"
    
    

async def usercredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exporta todos los usuarios y sus créditos actuales a CSV - Solo admins"""
    user_id = update.effective_user.id
    
    # Verificar si es admin
    if not await is_cookie_admin(user_id):
        await update.message.reply_text("❌ Solo administradores pueden usar este comando.")
        return
    
    await update.message.reply_text("⏳ Generando exportación de usuarios con créditos...")
    
    try:
        # Generar archivo
        filename = f"cookie_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath, message = await export_cookie_users_to_csv(filename)
        
        if not filepath:
            await update.message.reply_text(f"❌ {message}")
            return
        
        # Obtener estadísticas
        async with cookie_db_pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM cookie_users")
            total_credits = await conn.fetchval("SELECT COALESCE(SUM(credits), 0) FROM cookie_users")
            avg_credits = await conn.fetchval("SELECT COALESCE(AVG(credits), 0) FROM cookie_users")
            
            # Usuarios con créditos > 0
            active_users = await conn.fetchval("SELECT COUNT(*) FROM cookie_users WHERE credits > 0")
            
            # Top 5 usuarios con más créditos
            top_users = await conn.fetch(
                "SELECT user_id, username, credits FROM cookie_users ORDER BY credits DESC LIMIT 5"
            )
        
        # Construir caption
        caption = (
            f"👥 *Exportación de Usuarios - Cookie System*\n\n"
            f"📊 Estadísticas:\n"
            f"• Total usuarios: `{total_users}`\n"
            f"• Usuarios activos (>0 créditos): `{active_users}`\n"
            f"• Créditos totales en sistema: `{total_credits}`\n"
            f"• Promedio por usuario: `{int(avg_credits)}`\n\n"
            f"🏆 Top 5 usuarios:\n"
        )
        
        for i, user in enumerate(top_users, 1):
            name = user['username'] or f"ID:{user['user_id']}"
            caption += f"{i}. {name}: `{user['credits']}` créditos\n"
        
        caption += f"\n📁 Archivo: `{filename}`"
        
        # Enviar archivo
        await update.message.reply_document(
            document=open(filepath, 'rb'),
            caption=caption,
            parse_mode='Markdown'
        )
        
        # Limpiar archivo temporal
        import os
        os.remove(filepath)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error exportando: {e}")
        import traceback
        print(traceback.format_exc())
        

async def topcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el ranking de usuarios con más créditos - Solo admins"""
    user_id = update.effective_user.id
    
    if not await is_cookie_admin(user_id):
        await update.message.reply_text("❌ Solo administradores.")
        return
    
    try:
        async with cookie_db_pool.acquire() as conn:
            # Top 10 usuarios
            top_users = await conn.fetch(
                """
                SELECT user_id, username, first_name, credits, last_used
                FROM cookie_users
                ORDER BY credits DESC
                LIMIT 10
                """
            )
            
            # Estadísticas
            total = await conn.fetchval("SELECT COUNT(*) FROM cookie_users")
            sum_credits = await conn.fetchval("SELECT COALESCE(SUM(credits), 0) FROM cookie_users")
        
        text = "🏆 *RANKING DE USUARIOS - CRÉDITOS*\n\n"
        text += f"👥 Total usuarios: `{total}` | 💰 Total créditos: `{sum_credits}`\n\n"
        
        for i, user in enumerate(top_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            name = user['username'] or user['first_name'] or f"ID:{user['user_id']}"
            last_used = user['last_used'].strftime('%d/%m %H:%M') if isinstance(user['last_used'], datetime) else str(user['last_used'])[:16]
            
            text += (
                f"{medal} `{name}`\n"
                f"   💰 {user['credits']} créditos\n"
                f"   🕐 Último uso: {last_used}\n\n"
            )
        
        text += "📥 Usa `/usercredits` para descargar CSV completo"
        
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        



# --------------------------
# FUNCIONES DEL GENERADOR DE TARJETAS (integradas)
# --------------------------

@require_key
async def extra_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Limpiar datos residuales por si acaso
    keys_to_clear = ['entradas', 'template_base', 'modo', 'bin_len', 'cantidad']
    for key in keys_to_clear:
        if key in context.user_data:
            del context.user_data[key]
    
    await update.message.reply_text(
        "💳 *¡Bienvenido a 🏛️HadesExtra V1🏛️!*\n\n"
        "Luhn Engine │ BIN │ Extrapolador\n\n"
        "FLUJO RECOMENDADO:\n"
        "❶ Tienes lives → Extrapolar desde lives → genera BINs cercanos\n"
        "❷ Tienes BIN → Extrapolar BIN → genera variantes\n\n"
        "Elige una opción:\n"
        "[1] Extrapolar BIN ← opción principal\n"
        "[2] E extrapolar desde lives ★\n"
        "[3] Cargar BINs desde CSV\n"
        "[0] Salir\n\n"
        "⚠️ *Nota:* Usa /cancel en cualquier momento para salir y borrar datos.",
        parse_mode='Markdown'
    )
    return ESTADO_MENU

@require_key
async def menu_opcion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    opcion = update.message.text.strip()
    
    if opcion == "0":
        await update.message.reply_text("Hasta luego 👋")
        return ConversationHandler.END
    elif opcion == "1":
        await update.message.reply_text(
            "EXTRAPOLAR BIN\n\n"
            "Extrapola variantes de un BIN que ya tienes.\n"
            "El BIN real queda fijo — mismo banco, misma red.\n"
            "Solo cambian los dígitos de cuenta, generando vecinos.\n\n"
            "Ingresa tu BIN (6-10 dígitos) o un BIN completo con formato pipe."
        )
        return ESTADO_EXTRAPOLAR
    elif opcion == "2":
        await update.message.reply_text(
            "EXTRAPOLAR DESDE LIVES\n\n"
            "Pega tus lives (tarjetas activas conocidas) y el sistema detecta\n"
            "automáticamente el BIN en común para extrapolarlo.\n\n"
            "Cuantas más lives, más preciso el BIN detectado.\n"
            "Con 2-3 lives: solo se fijan posiciones 100% iguales.\n"
            "Con 10+ lives: se fijan posiciones que coinciden en 6/10 o más.\n\n"
            "Pega tus lives una por línea. Escribe FIN cuando termines."
        )
        context.user_data['entradas'] = []
        return ESTADO_LIVES
    elif opcion == "3":
        await update.message.reply_text(
            "CARGAR Y GENERAR DESDE CSV\n\n"
            "Carga un archivo CSV con BINs y genera tarjetas para cada uno.\n\n"
            "El CSV debe tener estas columnas:\n"
            "BIN, Brand, Type, Issuer, CountryName\n\n"
            "Ingresa la ruta del archivo CSV:"
        )
        return ESTADO_BIN
    else:
        await update.message.reply_text("Opción inválida. Elige una opción del menú.")
        return ESTADO_MENU

@require_key
async def recibir_bin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bin_input = update.message.text.strip()
    
    try:
        if "|" in bin_input:
            num = bin_input.split("|")[0].lower()
            if num and (any(c == "x" for c in num) or num.isdigit()):
                context.user_data['template_base'] = bin_input
            else:
                await update.message.reply_text("Formato inválido.")
                return ESTADO_EXTRAPOLAR
        elif bin_input.isdigit() and 6 <= len(bin_input) <= 10:
            try:
                template_base = construir_template_desde_bin(bin_input)
                context.user_data['template_base'] = template_base
                await update.message.reply_text(f"BIN base creado: {template_base}")
            except ValueError as e:
                await update.message.reply_text(f"Error: {e}")
                return ESTADO_EXTRAPOLAR
        else:
            await update.message.reply_text("Solo dígitos (6-10) o formato NUMERO|MM|AAAA|CVV.")
            return ESTADO_EXTRAPOLAR
        
        await update.message.reply_text(
            "¿Cómo quieres explorar?\n\n"
            "[1] CERCA PRIMERO (recomendado)\n"
            "Empieza siempre aquí. Busca números muy cercanos al tuyo.\n"
            "Alta probabilidad de encontrar variantes activas.\n\n"
            "[2] RANGO AMPLIO\n"
            "Úsalo cuando [1] ya no te da resultados nuevos,\n"
            "o cuando los que obtienes se parecen demasiado entre sí."
        )
        return ESTADO_MODO
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ESTADO_EXTRAPOLAR

@require_key
async def recibir_modo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    modo_input = update.message.text.strip()
    
    modo = "variado" if modo_input == "2" else "inteligente"
    context.user_data['modo'] = modo
    
    await update.message.reply_text(
        "¿Cuántos dígitos congelar como BIN fijo?\n\n"
        "[1] 8 dígitos ← Lo mejor para tarjetas actuales (Visa, MC, etc.)\n"
        "[2] 6 dígitos ← Solo si tus tarjetas son muy antiguas o con [1]\n"
        "no consigues nada diferente\n\n"
        "Nota: El BIN son los dígitos del banco. Más dígitos fijos = más\n"
        "precisión pero menos variación. Menos dígitos = más rango."
    )
    return ESTADO_BIN_LEN

@require_key
async def recibir_bin_len(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bin_len_input = update.message.text.strip()
    
    bin_len = 6 if bin_len_input == "2" else 8
    context.user_data['bin_len'] = bin_len
    
    await update.message.reply_text("¿Cuántos BINs extrapolar? (1-99, default 10):")
    return ESTADO_CANTIDAD

@require_key
async def recibir_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cantidad_input = update.message.text.strip()
    
    try:
        cantidad = int(cantidad_input) if cantidad_input else 10
        if not (1 <= cantidad <= 99):
            raise ValueError
    except ValueError:
        cantidad = 10
    
    context.user_data['cantidad'] = cantidad
    
    modo_display = "cerca primero" if context.user_data['modo'] == "inteligente" else "rango amplio"
    await update.message.reply_text(
        f"Extrapolando en modo '{modo_display}' | BIN fijo: {context.user_data['bin_len']} dígitos..."
    )
    
    try:
        template_base = context.user_data['template_base']
        templates = extrapolar_templates(
            template_base, 
            cantidad, 
            context.user_data['modo'], 
            context.user_data['bin_len']
        )
        
        respuesta = f"BINs EXTRAPOLADOS\n\n"
        respuesta += f"BIN base: {template_base}\n"
        respuesta += f"Extrapolados: {len(templates)}\n\n"
        
        for i, tpl in enumerate(templates, 1):
            respuesta += f"{i}. {tpl}\n"
        
        await update.message.reply_text(respuesta)
        
        # Terminar automáticamente después de mostrar los BINs extrapolados
        await update.message.reply_text("Proceso completado. Usa /extra para comenzar de nuevo.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ESTADO_CANTIDAD

@require_key
async def recibir_lives(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = update.message.text.strip()
    
    if texto.upper() == "FIN":
        entradas = context.user_data.get('entradas', [])
        if len(entradas) < 2:
            await update.message.reply_text("Necesitas al menos 2 tarjetas antes de continuar.")
            return ESTADO_LIVES
        
        try:
            # Detectar fecha común
            meses_lives = []
            anios_lives = []
            for e in entradas:
                partes = e.strip().split("|")
                if len(partes) >= 3:
                    m = partes[1].strip()
                    a = partes[2].strip()
                    if m.isdigit():
                        meses_lives.append(m.zfill(2))
                    if a.isdigit():
                        anios_lives.append(a)
            
            mes_auto = "xx"
            anio_auto = "20xx"
            if meses_lives:
                mes_comun, mes_freq = Counter(meses_lives).most_common(1)[0]
                if mes_freq / len(entradas) >= 0.60:
                    mes_auto = mes_comun
            if anios_lives:
                anio_comun, anio_freq = Counter(anios_lives).most_common(1)[0]
                if anio_freq / len(entradas) >= 0.60:
                    anio_auto = anio_comun
            
            # Detectar red común
            redes_detectadas = [
                info_red(_extraer_numero(e))["red"]
                for e in entradas
                if _extraer_numero(e).isdigit()
            ]
            red_comun = Counter(redes_detectadas).most_common(1)[0][0]
            cvv_len = _INFO_RED.get(red_comun, _INFO_RED["Otra"])["cvv"]
            cvv_auto = "x" * cvv_len
            
            # Determinar umbral según cantidad de lives
            n = len(entradas)
            if n <= 2: umbral = 0.99
            elif n == 3: umbral = 0.80
            elif n == 4: umbral = 0.75
            elif n <= 7: umbral = 0.70
            elif n <= 12: umbral = 0.65
            else: umbral = 0.60
            
            umbral_display = 100 if n <= 2 else int(umbral * 100)
            await update.message.reply_text(f"Analizando {n} lives con umbral {umbral_display}%...")
            
            template, reporte, bin_confiable = analizar_patron(
                entradas, umbral, mes_auto, anio_auto, cvv_auto
            )
            
            await update.message.reply_text(
                f"BIN DETECTADO\n\n"
                f"Números analizados: {len(entradas)}\n"
                f"BIN detectado: {template.split('|')[0]}\n"
                f"BIN completo: {template}\n"
                f"Red identificada: {red_comun}\n"
                f"BIN confiable: {bin_confiable}"
            )
            
            # Continuar con extrapolación
            await update.message.reply_text(
                f"Con base en tus lives, el BIN detectado es: {bin_confiable}\n"
                "Vamos a extrapolar variantes cercanas para ampliar el rango.\n\n"
                "¿Cómo quieres explorar?\n\n"
                "[1] CERCA PRIMERO (recomendado)\n"
                "[2] RANGO AMPLIO"
            )
            
            context.user_data['template_base'] = template
            return ESTADO_MODO
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return ESTADO_LIVES
    else:
        context.user_data['entradas'].append(texto)
        await update.message.reply_text(f"Recibido live #{len(context.user_data['entradas'])}")
        return ESTADO_LIVES

@require_key
async def recibir_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ruta = update.message.text.strip()
    
    try:
        # Aquí iría el código para cargar el CSV, pero como no tenemos la implementación completa
        # Simplemente mostramos un mensaje de error
        await update.message.reply_text(
            "Función de CSV no implementada en esta versión del bot.\n"
            "Por favor, usa las otras opciones disponibles."
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return ESTADO_BIN

@require_key
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela el proceso de /extra y limpia todos los datos temporales del usuario"""
    user_id = update.effective_user.id
    
    # Lista de claves temporales que usa /extra
    keys_to_clear = [
        'entradas',          # Para ESTADO_LIVES
        'template_base',     # Para ESTADO_EXTRAPOLAR, ESTADO_MODO, etc.
        'modo',               # Para ESTADO_MODO
        'bin_len',           # Para ESTADO_BIN_LEN
        'cantidad',          # Para ESTADO_CANTIDAD
        'proceso_extra'      # Flag adicional por si acaso
    ]
    
    # Limpiar datos del contexto del usuario
    for key in keys_to_clear:
        if key in context.user_data:
            del context.user_data[key]
    
    # Mensaje de confirmación
    await update.message.reply_text(
        "❌ *Proceso cancelado completamente.*\n\n"
        "✅ Se han eliminado todos los datos temporales.\n"
        "✅ Has salido del generador de tarjetas.\n\n"
        "Puedes usar /extra cuando necesites iniciar de nuevo.",
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END

# --------------------------
# RESTO DE FUNCIONES DEL BOT ORIGINAL
# --------------------------

@require_key
async def add_cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para agregar cookies de Amazon - Ahora soporta responder a mensajes"""
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        cookies_raw = None
        
        # CASO 1: El comando es respuesta a un mensaje
        if update.message.reply_to_message and update.message.reply_to_message.text:
            # Extraer el texto completo del mensaje respondido
            full_text = update.message.reply_to_message.text
            
            # Buscar donde empieza la cookie (session-id=)
            if "session-id=" in full_text:
                # Encontrar la posición de "session-id="
                cookie_start = full_text.find("session-id=")
                # Extraer desde session-id= hasta el final
                cookies_raw = full_text[cookie_start:].strip()
                
                # Limpiar si hay texto después de la cookie (saltos de línea, etc)
                if "\n\n" in cookies_raw:
                    cookies_raw = cookies_raw.split("\n\n")[0]
                if "\n" in cookies_raw:
                    # Verificar si la siguiente línea no continúa la cookie
                    lines = cookies_raw.split("\n")
                    cookie_lines = []
                    for line in lines:
                        if line.strip().startswith("session-") or line.strip().startswith("i18n-") or \
                        line.strip().startswith("lc-") or line.strip().startswith("ubid-") or \
                        line.strip().startswith("at-") or line.strip().startswith("sess-") or \
                        line.strip().startswith("sst-") or line.strip().startswith("x-") or \
                        line.strip().startswith("sso-"):
                            cookie_lines.append(line)
                        else:
                            break
                    cookies_raw = "; ".join(cookie_lines) if cookie_lines else lines[0]
            else:
                # Si no encuentra session-id, usar todo el mensaje (fallback)
                cookies_raw = full_text.strip()
            
            await update.message.reply_text("🍪 Extrayendo cookie del mensaje...")
        
        # CASO 2: El comando tiene argumentos (modo tradicional)
        elif len(context.args) >= 1:
            # Extraer cookies de los argumentos
            cookies_raw = " ".join(context.args)
        
        # Si no hay cookies en ningún caso
        if not cookies_raw:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa una de estas opciones:\n\n"
                "1️⃣ *Modo directo:*\n"
                "`/addcookie session-id=xxx; i18n-prefs=MXN; ...`\n\n"
                "2️⃣ *Respondiendo a un mensaje:*\n"
                "Responde al mensaje que contiene las cookies con `/addcookie`",
                parse_mode='Markdown'
            )
            return
        
        # LIMPIEZA CRÍTICA: Eliminar escapes de Telegram y formatear (Telegram MarkdownV2 escapa _ * [ ] ( ) ~ ` > # + - = | { } . !)
        cookies_fixed = cookies_raw.replace('\\_', '_').replace('\\*', '*').replace('\\[', '[').replace('\\]', ']').replace('\\(', '(').replace('\\)', ')').replace('\\~', '~').replace('\\`', '`').replace('\\>', '>').replace('\\#', '#').replace('\\+', '+').replace('\\-', '-').replace('\\=', '=').replace('\\|', '|').replace('\\{', '{').replace('\\}', '}').replace('\\.', '.').replace('\\!', '!').replace('\\"', '"').replace('"', '"')
        cookies_formatted = cookies_fixed.replace('; ', ';').strip()
        
        # Guardar las cookies formateadas para este usuario
        amazon_cookies[user_id] = cookies_formatted
        
        # Mensaje de confirmación (ocultar parte de la cookie por seguridad)
        cookie_preview = cookies_formatted[:50] + "..." if len(cookies_formatted) > 50 else cookies_formatted
        await update.message.reply_text(
            f"✅ *Cookies de Amazon guardadas correctamente.*\n\n"
            f"🍪 Preview: `{cookie_preview}`\n"
            f"📏 Longitud: {len(cookies_formatted)} caracteres\n\n"
            f"Ahora puedes usar los comandos `/az` y `/azmass` sin necesidad de especificar las cookies.\n\n"
            f"Para cambiar las cookies, usa `/addcookie` de nuevo.",
            parse_mode='Markdown'
        )
        
        # Intentar eliminar el mensaje original con las cookies por seguridad (solo si es respuesta)
        if update.message.reply_to_message:
            try:
                await update.message.reply_to_message.delete()
                await update.message.delete()
            except Exception as e:
                print(f"No se pudo eliminar el mensaje con cookies: {e}")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error al procesar cookies: {e}")

@require_key
async def amazon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        
        # Verificar si ha pasado el tiempo mínimo desde la última ejecución de /az
        now = datetime.now()
        if user_id in last_az_time:
            time_diff = (now - last_az_time[user_id]).total_seconds()
            if time_diff < AZ_COOLDOWN:
                remaining_time = AZ_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /az."
                )
                return
        
        # Verificar si el usuario tiene cookies guardadas
        if user_id not in amazon_cookies:
            await update.message.reply_text(
                "❌ No tienes cookies de Amazon guardadas.\n"
                "Usa /addcookie <cookies_amazon> primero."
            )
            return
        
        # Verificar si se proporcionaron datos de la tarjeta
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/az <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/az 4532640527811643|12|2025|123"
            )
            return
        
        # Extraer datos de la tarjeta
        card_info = context.args[0]
        
        # Validar formato de la tarjeta
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener las cookies guardadas del usuario
        cookies = amazon_cookies[user_id]
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Enviar mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación de Amazon...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # MODIFICACIÓN: Pasar el user_id a la función amazon_check
        result = await amazon_check(card_info, cookies, user_id)
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Approved":
            status_icon = "✅ Approved"
            resultado = "APROBADA"
        elif status_raw == "❌ Declined":
            status_icon = "❌ Declined"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje de respuesta con el estilo de la imagen
        # Usando el formato <code> para que sea copiable
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Amazon Check\n"
            "🔥 Comando: /az\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Editar el mensaje de procesamiento con el resultado
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Approved", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Approved":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        # Actualizar el tiempo de la última ejecución de /az
        last_az_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@require_key
async def amazon_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Control de tiempo
        now = datetime.now()
        if user_id in last_azmass_time:
            time_diff = (now - last_azmass_time[user_id]).total_seconds()
            if time_diff < AZMASS_COOLDOWN:
                remaining_time = AZMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /azmass."
                )
                return
        
        # Verificar cookies
        if user_id not in amazon_cookies:
            await update.message.reply_text(
                "❌ No tienes cookies de Amazon guardadas.\n"
                "Usa /addcookie <cookies_amazon> primero."
            )
            return
        
        cookies = amazon_cookies[user_id]
        lines = []
        
        # Función auxiliar para limpiar y extraer tarjetas
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            # Limpiar HTML y emojis
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                # Separar por |
                parts = [p.strip() for p in line.split('|')]
                
                # Debe tener exactamente 4 partes
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                # Validar que mes y año sean números
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                # Validar que el CVV tenga 3 o 4 dígitos (o sea rnd/xxx)
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                # Validar cantidad de dígitos: 15 o 16
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        # 1. Intentar extraer del mensaje respondido
        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10] # Limitar a 10

        # 2. Fallback: Intentar extraer del mensaje actual
        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /azmass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
                "Ejemplo VISA: 4532640527811643|12|2025|123"
            )
            return

        # Mostrar procesamiento
        processed_count = 0
        total_cards = len(lines)
        
        processing_message = await update.message.reply_text(
            "⏳ Procesando tarjetas por favor espere...\n"
            f"Procesadas: 0/{total_cards}"
        )
        
        current_processing_message = processing_message
        
        async def result_callback(index, result, card_data, user_id):
            nonlocal processed_count, current_processing_message
            await send_card_result(update, context, index, result, card_data, user_id, update)
            processed_count += 1
            
            if processed_count < total_cards:
                new_msg = await update.message.reply_text(
                    "⏳ Procesando tarjetas por favor espere...\n"
                    f"Procesadas: {processed_count}/{total_cards}"
                )
                try:
                    await current_processing_message.delete()
                except:
                    pass
                current_processing_message = new_msg
            else:
                try:
                    await current_processing_message.delete()
                except:
                    pass
        
        last_azmass_time[user_id] = now
        asyncio.create_task(amazon_check_multiple(lines, cookies, user_id, result_callback))
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

def validate_input(month: str, year: str, cvv: str) -> bool:
    try:
        month_int = int(month)
        year_int = int(year)
        if month_int < 1 or month_int > 12:
            return False
        if year_int < 2000 or year_int > 2100:
            return False
        if cvv not in ("rnd", "xxx", "xxxx", "000", "0000") and (len(cvv) not in (3, 4) or not cvv.isdigit()):
            return False
        return True
    except ValueError:
        return False

@require_key
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el texto completo del mensaje
        message_text = update.message.text
        
        # Verificar si se proporcionaron argumentos
        if len(message_text.split()) < 2:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa: /gen <BIN>|<mes>|<año>|<cvv>\n"
                "Ejemplo: /gen 457249651136xxxx|06|2029|xxx"
            )
            return
        
        # Extraer los argumentos después de /gen
        args = message_text.split()[1]
        parts = args.split("|")
        
        if len(parts) < 3 or len(parts) > 4:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa: /gen <BIN>|<mes>|<año>|<cvv>\n"
                "Ejemplo: /gen 457249651136xxxx|06|2029|xxx"
            )
            return
        
        number, month, year = parts[:3]
        cvv = parts[3] if len(parts) == 4 else "rnd"
        
        # Validar que no haya partes vacías
        if not number or not month or not year:
            await update.message.reply_text(
                "❌ Error: Verifica que todos los campos tengan valor\n"
                "Ejemplo: /gen 457249651136xxxx|06|2029|xxx"
            )
            return
        
        if len(year) == 2:
            year = "20" + year
        
        if not validate_input(month, year, cvv):
            await update.message.reply_text("❌ Entradas inválidas. Verifica el mes, año y CVV.")
            return
        
        # Generar las tarjetas usando el nuevo generador
        cards = CreditCardGenerator.generate_cc(number, month, year, cvv)
        
        # Limitar a 10 tarjetas
        cards = cards[:10]
        
        # Extraer los números de tarjeta para procesarlos
        card_numbers = [card.split("|")[0] for card in cards]
        
        # Generar CVVs si es necesario
        cvv_list = []
        for card in card_numbers:
            if cvv.lower() in ("rnd", "xxx", "xxxx"):
                cvv_list.append(CreditCardGenerator._generate_cvv(cvv, 3))
            elif cvv in ("000", "0000"):
                cvv_list.append(cvv)
            else:
                cvv_list.append(cvv)
        
        # Obtener información del BIN
        bin_info = get_card_info(number[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Crear el mensaje con el nuevo estilo
        response = (
            "🏛️ Hades V1 🏛️\n"
            "----------------------------\n"
            f"CC: <code>{number}|{month}|{year}</code> <a href='tg://copy?text={number}|{month}|{year}'></a>\n"
            "----------------------------\n"
        )
        
        # Agregar las tarjetas con formato de lista y enlaces copiables (sin emoji 📋)
        for i, card in enumerate(cards):
            card_parts = card.split("|")
            card_number = card_parts[0]
            exp_month = card_parts[1]
            exp_year = card_parts[2]
            card_cvv = card_parts[3]
            
            # Crear enlace copiable para la tarjeta (sin emoji 📋)
            card_with_exp = f"{card_number}|{exp_month}|{exp_year}|{card_cvv}"
            response += f"• <code>{card_with_exp}</code> <a href='tg://copy?text={card_with_exp}'></a>\n"
        
        # Agregar información del BIN con fondo verde (sin emoji 📋)
        response += (
            "----------------------------\n"
            "🔥 <b>Bin:</b> <code>" + number[:6] + "</code> <a href='tg://copy?text=" + number[:6] + "'></a>\n"
            "🔥 <b>Marca:</b> " + marca + "\n"
            "🔥 <b>Tipo de tarjeta:</b> " + tipo_tarjeta + "\n"
            "🔥 <b>Nivel de tarjeta:</b> " + nivel_tarjeta + "\n"
            "🔥 <b>Banco:</b> " + banco + "\n"
            "🔥 <b>Teléfono:</b> " + telefono + "\n"
            "🔥 <b>País:</b> " + pais + "\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el teclado con botones (restaurado)
        keyboard = [
            [InlineKeyboardButton("🔄 Regenerar", callback_data=f"regenerate|{number}|{month}|{year}|{cvv}")],
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(response, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@require_key
async def generate0(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el texto completo del mensaje
        message_text = update.message.text
        
        # Verificar si se proporcionaron argumentos
        if len(message_text.split()) < 2:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa: /gen0 <cantidad>\n"
                "Ejemplo: /gen0 10"
            )
            return
        
        # Extraer la cantidad después de /gen0
        args = message_text.split()[1]
        
        try:
            quantity = int(args)
            if quantity <= 0 or quantity > 50:
                raise ValueError()
        except ValueError:
            await update.message.reply_text(
                "❌ Cantidad inválida. Usa un número entre 1 y 50.\n"
                "Ejemplo: /gen0 10"
            )
            return
        
        # Generar tarjetas completamente aleatorias
        cards = []
        for _ in range(quantity):
            # Generar un BIN aleatorio (primeros 6 dígitos)
            bin_prefix = str(random.randint(400000, 499999))  # Rango de Visa
            
            # Generar una tarjeta aleatoria con ese BIN
            card = CreditCardGenerator.generate_cc(bin_prefix + "xxxxxxxx", "rnd", "rnd", "rnd", 1)
            cards.extend(card)
        
        # Extraer los datos de las tarjetas
        card_data = []
        for card in cards:
            parts = card.split("|")
            card_data.append({
                'number': parts[0],
                'month': parts[1],
                'year': parts[2],
                'cvv': parts[3]
            })
        
        # Obtener información del BIN para la primera tarjeta
        bin_info = get_card_info(card_data[0]['number'][:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Crear los enlaces de las tarjetas
        card_links = "\n".join(
            f"<code>{card['number']}|{card['month']}|{card['year']}|{card['cvv']}</code> "
            f"<a href='tg://copy?text={card['number']}|{card['month']}|{card['year']}|{card['cvv']}'></a>"
            for card in card_data)
        
        response = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            f"TARJETAS ALEATORIAS ({quantity} generadas)\n"
            "----------------------------\n"
            f"{card_links}\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
        )
        
        # Crear el teclado con botones (restaurado)
        keyboard = [
            [InlineKeyboardButton("🔄 Generar más", callback_data=f"generate0|{quantity}")],
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(response, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def regenerate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        # Extraer los datos del callback
        parts = query.data.split("|")
        if len(parts) < 5:
            await query.edit_message_text("❌ Error en los datos de regeneración.")
            return
        
        action = parts[0]
        
        if action == "regenerate":
            number, month, year, cvv = parts[1:5]
            
            # Generar las tarjetas usando el nuevo generador
            cards = CreditCardGenerator.generate_cc(number, month, year, cvv)
            
            # Limitar a 10 tarjetas
            cards = cards[:10]
            
            # Extraer los números de tarjeta para procesarlos
            card_numbers = [card.split("|")[0] for card in cards]
            
            # Generar CVVs si es necesario
            cvv_list = []
            for card in card_numbers:
                if cvv.lower() in ("rnd", "xxx", "xxxx"):
                    cvv_list.append(CreditCardGenerator._generate_cvv(cvv, 3))
                elif cvv in ("000", "0000"):
                    cvv_list.append(cvv)
                else:
                    cvv_list.append(cvv)
            
            # Obtener información del BIN
            bin_info = get_card_info(number[:6])
            marca = bin_info['Marca'] if bin_info else "Desconocido"
            tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
            nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
            banco = bin_info['Banco'] if bin_info else "Desconocido"
            telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
            pais = bin_info['País'] if bin_info else "Desconocido"
            
            # Crear el mensaje con el nuevo estilo (actualizado)
            response = (
                "🏛️ Hades V1 🏛️\n"
                "----------------------------\n"
                f"CC: <code>{number}|{month}|{year}</code> <a href='tg://copy?text={number}|{month}|{year}'></a>\n"
                "----------------------------\n"
            )
            
            # Agregar las tarjetas con formato de lista y enlaces copiables (sin emoji 📋)
            for i, card in enumerate(cards):
                card_parts = card.split("|")
                card_number = card_parts[0]
                exp_month = card_parts[1]
                exp_year = card_parts[2]
                card_cvv = card_parts[3]
                
                # Crear enlace copiable para la tarjeta (sin emoji 📋)
                card_with_exp = f"{card_number}|{exp_month}|{exp_year}|{card_cvv}"
                response += f"• <code>{card_with_exp}</code> <a href='tg://copy?text={card_with_exp}'></a>\n"
            
            # Agregar información del BIN con fondo verde (sin emoji 📋)
            response += (
                "----------------------------\n"
                "🔥 <b>Bin:</b> <code>" + number[:6] + "</code> <a href='tg://copy?text=" + number[:6] + "'></a>\n"
                "🔥 <b>Marca:</b> " + marca + "\n"
                "🔥 <b>Tipo de tarjeta:</b> " + tipo_tarjeta + "\n"
                "🔥 <b>Nivel de tarjeta:</b> " + nivel_tarjeta + "\n"
                "🔥 <b>Banco:</b> " + banco + "\n"
                "🔥 <b>Teléfono:</b> " + telefono + "\n"
                "🔥 <b>País:</b> " + pais + "\n"
                "----------------------------\n"
                "🔥 <b>Developer:</b> @Chack0071\n"
                "----------------------------\n"
                "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
                "----------------------------\n"
            )
            
            # Crear el teclado con botones (restaurado)
            keyboard = [
                [InlineKeyboardButton("🔄 Regenerar", callback_data=f"regenerate|{number}|{month}|{year}|{cvv}")],
                [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(response, reply_markup=reply_markup, parse_mode="HTML")
        
        elif action == "generate0":
            try:
                quantity = int(parts[1])
                if quantity <= 0 or quantity > 50:
                    raise ValueError()
            except ValueError:
                await query.edit_message_text("❌ Cantidad inválida.")
                return
            
            # Generar tarjetas completamente aleatorias
            cards = []
            for _ in range(quantity):
                # Generar un BIN aleatorio (primeros 6 dígitos)
                bin_prefix = str(random.randint(400000, 499999))  # Rango de Visa
                
                # Generar una tarjeta aleatoria con ese BIN
                card = CreditCardGenerator.generate_cc(bin_prefix + "xxxxxxxx", "rnd", "rnd", "rnd", 1)
                cards.extend(card)
            
            # Extraer los datos de las tarjetas
            card_data = []
            for card in cards:
                parts = card.split("|")
                card_data.append({
                    'number': parts[0],
                    'month': parts[1],
                    'year': parts[2],
                    'cvv': parts[3]
                })
            
            # Obtener información del BIN para la primera tarjeta
            bin_info = get_card_info(card_data[0]['number'][:6])
            marca = bin_info['Marca'] if bin_info else "Desconocido"
            tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
            nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
            banco = bin_info['Banco'] if bin_info else "Desconocido"
            telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
            pais = bin_info['País'] if bin_info else "Desconocido"
            
            # Crear los enlaces de las tarjetas
            card_links = "\n".join(
                f"<code>{card['number']}|{card['month']}|{card['year']}|{card['cvv']}</code> "
                f"<a href='tg://copy?text={card['number']}|{card['month']}|{card['year']}|{card['cvv']}'></a>"
                for card in card_data)
            
            response = (
                "----------------------------\n"
                "🏛️Hades V1🏛️\n"
                "----------------------------\n"
                f"TARJETAS ALEATORIAS ({quantity} generadas)\n"
                "----------------------------\n"
                f"{card_links}\n"
                "----------------------------\n"
                f"🔥 Marca: {marca}\n"
                f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
                f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
                f"🔥 Banco: {banco}\n"
                f"🔥 Teléfono: {telefono}\n"
                f"🔥 País: {pais}\n"
                "----------------------------\n"
                "🔥 <b>Developer:</b> @Chack0071\n"
                "----------------------------\n"
            )
            
            # Crear el teclado con botones (restaurado)
            keyboard = [
                [InlineKeyboardButton("🔄 Generar más", callback_data=f"generate0|{quantity}")],
                [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(response, reply_markup=reply_markup, parse_mode="HTML")
    
    except Exception as e:
        await query.edit_message_text(f"Error: {e}")

@require_key
async def bin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bin_number = update.message.text.split()[1]
        
        if len(bin_number) != 6 or not bin_number.isdigit():
            await update.message.reply_text("❌ El BIN debe tener exactamente 6 dígitos.")
            return
        
        bin_info = get_card_info(bin_number)
        if not bin_info:
            await update.message.reply_text("❌ No se encontró información para el BIN proporcionado.")
            return
        
        marca = bin_info['Marca']
        tipo_tarjeta = bin_info['Tipo de tarjeta']
        nivel_tarjeta = bin_info['Nivel de tarjeta']
        banco = bin_info['Banco']
        telefono = bin_info['Teléfono']
        pais = bin_info['País']
        
        response = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            f"🔥 Bin: <code>{bin_number}</code> <a href='tg://copy?text={bin_number}'></a>\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
        )
        
        await update.message.reply_text(response, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@require_key
async def fake_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = update.message.text.split()
        if len(args) < 2:
            await update.message.reply_text("❌ Formato incorrecto. Usa /fake <país> (ejemplo: /fake mx).")
            return
        
        country_code = args[1].lower()
        
        if country_code == "mx":
            fake_local = Faker('es_MX')
            country_name = "México"
            codigo_pais = "+52"
        elif country_code == "us":
            fake_local = Faker('en_US')
            country_name = "Estados Unidos"
            codigo_pais = "+1"
        elif country_code == "jp":
            fake_local = Faker('ja_JP')
            country_name = "Japón"
            codigo_pais = "+81"
        else:
            await update.message.reply_text("❌ País no soportado. Usa /fake mx, /fake us o /fake jp.")
            return
        
        estado = fake_local.state()
        ciudad = fake_local.city()
        codigo_postal = fake_local.postcode_in_state(state_abbr=estado)
        direccion = fake_local.street_address()
        telefono = f"{codigo_pais} {fake_local.numerify('###-###-####')}"
        email = fake_local.email()
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔹 Generador de datos\n"
            f"🔹 Dirección: <code>{direccion}</code> <a href='tg://copy?text={direccion}'></a>\n"
            f"🔹 Ciudad: <code>{ciudad}</code> <a href='tg://copy?text={ciudad}'></a>\n"
            f"🔹 Estado/región/provincia: <code>{estado}</code> <a href='tg://copy?text={estado}'></a>\n"
            f"🔹 Teléfono: <code>{telefono}</code> <a href='tg://copy?text={telefono}'></a>\n"
            f"🔹 Código Postal: <code>{codigo_postal}</code> <a href='tg://copy?text={codigo_postal}'></a>\n"
            f"🔹 País: <code>{country_name}</code>\n"
            f"🔹 Email: <code>{email}</code> <a href='tg://copy?text={email}'></a>\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
        )
        
        await update.message.reply_text(response_message, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        
# Función para administradores: importar datos desde un archivo TXT
# Función para administradores: importar datos desde un archivo TXT (versión mejorada)
async def import_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Importar datos desde archivo TXT - Solo Superadmins"""
    global SUPERADMINS  # ← MOVER AQUÍ AL INICIO

    try:
        user_id = update.effective_user.id
        
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden importar datos.\n\n"
                "⚠️ Esta función permite restaurar usuarios, admins y superadmins "
                "desde un archivo de backup.",
                parse_mode='Markdown'
            )
            return
        
        # Obtener archivo
        file = None
        if update.message.document:
            file = update.message.document
        elif update.message.reply_to_message and update.message.reply_to_message.document:
            file = update.message.reply_to_message.document
        
        if not file:
            await update.message.reply_text("❌ Responde al archivo TXT con /import")
            return
        
        if not file.file_name.lower().endswith('.txt'):
            await update.message.reply_text("❌ El archivo debe ser .txt")
            return
        
        # Descargar
        file_obj = await context.bot.get_file(file.file_id)
        file_content = await file_obj.download_as_bytearray()
        content = file_content.decode('utf-8')
        
        lines = content.split('\n')
        admins_imported = 0
        superadmins_imported = 0
        users_imported = 0
        planes_asignados = {}
        errors = []
        current_section = None
        
        # Usar la conexión del KeyManager (PostgreSQL o SQLite)
        conn = key_manager._get_conn()
        cursor = conn.cursor()
        
        for line in lines:
            line = line.strip()
            
            if "SUPERADMINS:" in line:
                current_section = "superadmins"
                continue
            elif "ADMINISTRADORES:" in line:
                current_section = "admins"
                continue
            elif "USUARIOS CON CLAVES ACTIVAS:" in line:
                current_section = "users"
                continue
            
            # Procesar superadmins
            if current_section == "superadmins" and line.startswith("ID: "):
                try:
                    # Formato: ID: 123456789 | Rol: 👑 Creador
                    parts = line.split(" | ")
                    sa_id = int(parts[0].replace("ID: ", "").strip())
                    is_creator = "Creador" in parts[1] if len(parts) > 1 else False
                    
                    # Agregar a superadmins
                    if add_superadmin_to_db(sa_id, user_id, is_creator):
                        if sa_id not in SUPERADMINS:
                            SUPERADMINS.append(sa_id)
                        superadmins_imported += 1
                        
                        # También agregar como admin
                        if not key_manager.is_admin(sa_id):
                            key_manager.add_admin(sa_id, user_id)
                except Exception as e:
                    errors.append(f"Error superadmin: {str(e)[:50]}")
            
            # Procesar admins
            elif current_section == "admins" and line.startswith("ID: "):
                try:
                    admin_id = int(line.replace("ID: ", "").strip().split()[0])
                    if not key_manager.is_admin(admin_id):
                        key_manager.add_admin(admin_id, user_id)
                        admins_imported += 1
                except ValueError:
                    errors.append(f"ID admin inválido: {line}")
            
            # Procesar usuarios con plan AUTOMÁTICO del archivo
            elif current_section == "users" and line.startswith("ID: "):
                try:
                    # Extraer plan del archivo
                    plan_detectado = "1SEMA"  # Default
                    if "| Plan:" in line:
                        plan_part = line.split("| Plan:")[-1].strip()
                        plan_detectado = plan_part.split()[0].upper()
                        if plan_detectado not in ['OLIMPO', '1MES', '15DIAS', '1SEMA']:
                            plan_detectado = "1SEMA"
                    
                    parts = line.split(" | ")
                    user_id_part = int(parts[0].replace("ID: ", "").strip())
                    key_part = parts[1].replace("Clave: ", "").strip()
                    
                    # Extraer días
                    days = 30
                    for part in parts:
                        if "Activa (" in part and "días" in part:
                            try:
                                days_str = part.split("(")[1].split(" días")[0]
                                days = int(days_str)
                            except:
                                pass
                        elif "Expirada" in part:
                            days = 0
                    
                    expires_at = datetime.now() + timedelta(days=days) if days > 0 else datetime.now()
                    
                    # Detectar si usamos PostgreSQL o SQLite
                    is_postgres = key_manager.postgres_url and hasattr(key_manager, 'postgres_url')
                    
                    if is_postgres:
                        # PostgreSQL usa %s y TRUE/FALSE
                        cursor.execute('''
                        INSERT INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (key) DO UPDATE SET
                            plan = EXCLUDED.plan,
                            expires_at = EXCLUDED.expires_at,
                            days = EXCLUDED.days,
                            used = EXCLUDED.used
                        ''', (key_part, plan_detectado, datetime.now(), expires_at, days, user_id_part, True))
                        
                        cursor.execute('''
                        INSERT INTO active_keys (user_id, key, plan, activated_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                            key = EXCLUDED.key,
                            plan = EXCLUDED.plan,
                            activated_at = EXCLUDED.activated_at,
                            expires_at = EXCLUDED.expires_at
                        ''', (user_id_part, key_part, plan_detectado, datetime.now(), expires_at))
                    else:
                        # SQLite usa ? y 1/0
                        cursor.execute('''
                        INSERT OR REPLACE INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (key_part, plan_detectado, datetime.now().isoformat(), 
                              expires_at.isoformat(), days, user_id_part, 1))
                        
                        cursor.execute('''
                        INSERT OR REPLACE INTO active_keys (user_id, key, plan, activated_at, expires_at)
                        VALUES (?, ?, ?, ?, ?)
                        ''', (user_id_part, key_part, plan_detectado, 
                              datetime.now().isoformat(), expires_at))
                    
                    users_imported += 1
                    planes_asignados[plan_detectado] = planes_asignados.get(plan_detectado, 0) + 1
                        
                except Exception as e:
                    errors.append(f"Línea error: {str(e)[:50]}")
        
        conn.commit()
        conn.close()
        
        # Recargar superadmins en memoria
        SUPERADMINS = load_superadmins_from_db()
        
        # Resumen
        response = f"✅ Importación completada\n\n"
        response += f"👑 Superadmins importados: {superadmins_imported}\n"
        response += f"👮 Admins importados: {admins_imported}\n"
        response += f"👥 Usuarios importados: {users_imported}\n\n"
        response += f"📊 Planes asignados:\n"
        for plan, cantidad in planes_asignados.items():
            response += f"  • {plan}: {cantidad}\n"
        
        if errors:
            response += f"\n⚠️ Errores ({len(errors)}): {errors[0]}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        import traceback
        print(traceback.format_exc())

# Función para administradores: restaurar todas las claves del archivo
async def restore_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restaurar todas las claves - Solo Superadmins"""
    try:
        user_id = update.effective_user.id
        
        if not is_superadmin(user_id):
            await update.message.reply_text(
                "❌ *Acceso Denegado*\n\n"
                "Solo los *Superadmins* pueden restaurar datos.",
                parse_mode='Markdown'
            )
            return
        
        # Verificar si se proporcionó un archivo
        file = None
        
        if update.message.document:
            file = update.message.document
        elif update.message.reply_to_message and update.message.reply_to_message.document:
            file = update.message.reply_to_message.document
        
        if not file:
            await update.message.reply_text(
                "❌ Debes proporcionar un archivo TXT exportado previamente.\n"
                "Usa: /restoreall (respondiendo a un archivo TXT)"
            )
            return
        
        # Verificar que sea un archivo TXT
        file_name = file.file_name
        if not file_name.lower().endswith('.txt'):
            await update.message.reply_text("❌ El archivo debe ser un archivo TXT (.txt)")
            return
        
        # Descargar el archivo
        file_obj = await context.bot.get_file(file.file_id)
        file_content = await file_obj.download_as_bytearray()
        content = file_content.decode('utf-8')
        
        # Procesar el contenido del archivo
        lines = content.split('\n')
        
        # Contadores para estadísticas
        users_restored = 0
        errors = []
        
        # Bandera para saber en qué sección estamos
        current_section = None
        
        for line in lines:
            line = line.strip()
            
            # Detectar secciones
            if "USUARIOS CON CLAVES ACTIVAS:" in line:
                current_section = "users"
                continue
            
            # Procesar según la sección actual
            if current_section == "users" and line.startswith("ID: "):
                try:
                    # Formato esperado: ID: 123456789 | Clave: ABC123 | Estado: Activa (10 días) | Peticiones: 0/3
                    parts = line.split(" | ")
                    if len(parts) >= 2:
                        user_id_part = parts[0].replace("ID: ", "").strip()
                        key_part = parts[1].replace("Clave: ", "").strip()
                        
                        user_id_val = int(user_id_part)
                        
                        # Extraer los días restantes del estado
                        days_remaining = 30  # Valor por defecto
                        if len(parts) >= 3:
                            status_part = parts[2].replace("Estado: ", "").strip()
                            if "Activa (" in status_part and " días)" in status_part:
                                try:
                                    days_str = status_part.split("Activa (")[1].split(" días)")[0]
                                    days_remaining = int(days_str)
                                except:
                                    pass
                        
                        # Extraer plan si existe
                        plan_detectado = "1SEMA"
                        if "| Plan:" in line:
                            plan_part = line.split("| Plan:")[-1].strip().split()[0]
                            if plan_part in ['OLIMPO', '1MES', '15DIAS', '1SEMA']:
                                plan_detectado = plan_part
                        
                        # Restaurar la clave
                        conn = key_manager._get_conn()
                        cursor = conn.cursor()
                        
                        # Calcular la nueva fecha de expiración
                        new_expires_at = datetime.now() + timedelta(days=days_remaining)
                        
                        is_postgres = key_manager.postgres_url and hasattr(key_manager, 'postgres_url')
                        
                        if is_postgres:
                            cursor.execute('''
                            INSERT INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (key) DO UPDATE SET
                                plan = EXCLUDED.plan,
                                expires_at = EXCLUDED.expires_at,
                                days = EXCLUDED.days,
                                used = EXCLUDED.used
                            ''', (key_part, plan_detectado, datetime.now(), new_expires_at, days_remaining, user_id_val, True))
                            
                            cursor.execute('''
                            INSERT INTO active_keys (user_id, key, plan, activated_at, expires_at)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (user_id) DO UPDATE SET
                                key = EXCLUDED.key,
                                plan = EXCLUDED.plan,
                                activated_at = EXCLUDED.activated_at,
                                expires_at = EXCLUDED.expires_at
                            ''', (user_id_val, key_part, plan_detectado, datetime.now(), new_expires_at))
                        else:
                            cursor.execute('''
                            INSERT OR REPLACE INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''', (key_part, plan_detectado, datetime.now().isoformat(), 
                                  new_expires_at.isoformat(), days_remaining, user_id_val, 1))
                            
                            cursor.execute('''
                            INSERT OR REPLACE INTO active_keys (user_id, key, plan, activated_at, expires_at)
                            VALUES (?, ?, ?, ?, ?)
                            ''', (user_id_val, key_part, plan_detectado, 
                                  datetime.now().isoformat(), new_expires_at))
                        
                        conn.commit()
                        conn.close()
                        
                        users_restored += 1
                except ValueError:
                    errors.append(f"ID de usuario inválido: {line}")
                except Exception as e:
                    errors.append(f"Error procesando línea: {line} - {str(e)}")
        
        # Enviar resumen de la restauración
        response = (
            f"✅ Restauración completada\n\n"
            f"🔹 Usuarios restaurados: {users_restored}\n"
        )
        
        if errors:
            response += f"\n❌ Errores ({len(errors)}):\n"
            for i, error in enumerate(errors[:5]):
                response += f"  - {error}\n"
            if len(errors) > 5:
                response += f"  - ... y {len(errors) - 5} errores más\n"
        
        await update.message.reply_text(response)
    
    except Exception as e:
        await update.message.reply_text(f"Error al restaurar datos: {e}")
        
        
        

# Variables para controlar el tiempo entre ejecuciones de comandos específicos
last_di_time = {}  # Diccionario para registrar la última ejecución de /di por usuario
last_dimass_time = {}  # Diccionario para registrar la última ejecución de /dimass por usuario
last_ph_time = {}  # Diccionario para registrar la última ejecución de /ph por usuario
last_phmass_time = {}  # Diccionario para registrar la última ejecución de /phmass por usuario
last_eu_time = {}  # Diccionario para registrar la última ejecución de /eu por usuario
last_eumass_time = {}  # Diccionario para registrar la última ejecución de /eumass por usuario
last_wo_time = {}  # Diccionario para registrar la última ejecución de /wo por usuario
last_womass_time = {}  # Diccionario para registrar la última ejecución de /womass por usuario
# Variables de cooldown para Atlantic
last_at_time = {}
last_atmass_time = {}
# Variables de cooldown para Cronos (PayPal $0.10) - solo single con antispam 30s
last_cr_time = {}
CR_COOLDOWN = 30

DI_COOLDOWN = 7  # Tiempo mínimo en segundos entre ejecuciones de /di
DIMASS_COOLDOWN = 20  # Tiempo mínimo en segundos entre ejecuciones de /dimass
PH_COOLDOWN = 7  # Tiempo mínimo en segundos entre ejecuciones de /ph
PHMASS_COOLDOWN = 20  # Tiempo mínimo en segundos entre ejecuciones de /phmass
EU_COOLDOWN = 7  # Tiempo mínimo en segundos entre ejecuciones de /eu
EUMASS_COOLDOWN = 20  # Tiempo mínimo en segundos entre ejecuciones de /eumass
WO_COOLDOWN = 7  # Tiempo mínimo en segundos entre ejecuciones de /wo
WOMASS_COOLDOWN = 20  # Tiempo mínimo en segundos entre ejecuciones de /womass
# Variables de cooldown para Atlantic
AT_COOLDOWN = 7
ATMASS_COOLDOWN = 20
# Cronos cooldown ya definido arriba (30s single only)

# Función de callback para enviar resultados de tarjetas en tiempo real
async def send_gate_result(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, result: dict, card_data: str, user_id: int, original_update: Update, gate_name: str):
    """
    Función de callback para enviar resultados de tarjetas en tiempo real
    """
    try:
        
         # SANITIZAR el mensaje antes de mostrarlo
        if "message" in result:
            result["message"] = sanitize_gate_response(result["message"])
        
        # Validar formato de la tarjeta
        card_parts = card_data.split("|")
        if len(card_parts) != 4:
            await original_update.message.reply_text(
                f"❌ Formato de tarjeta incorrecto en la tarjeta {index+1}: {card_data}"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = result["status"]
            resultado = "ERROR"
        
        # Construir mensaje de respuesta con el estilo de la imagen
        # Usando el formato <code> para que sea copiable
        
        # El enlace a la tarjeta (copiable con formato code)
        card_code = f"<code>{card_data}</code> <a href='tg://copy?text={card_data}'></a>"
        
        # El enlace al usuario (copiable con formato code)
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        # Determinar el comando y el gate
        gate_commands = {
            "Atlantida": "/at",      
            "Euridice": "/di",
            "Philotes": "/ph",
            "Eurinias": "/eu",
            "Wojtek": "/wo"
        }
        
        gate_emojis = {
            "Atlantida": "🌊",      
            "Euridice": "🔱",
            "Philotes": "💎",
            "Eurinias": "⚡",
            "Wojtek": "🔥"
        }
        
        command = gate_commands.get(gate_name, "/di")
        emoji = gate_emojis.get(gate_name, "🔥")
        
        
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            f"{emoji} {gate_name} Check\n"
            f"{emoji} Comando: {command}\n"
            "----------------------------\n"
            f"{emoji} {card_code}\n"
            f"{emoji} Status: {status_icon}\n"
            f"{emoji} Response: {result['message']}\n"
            f"{emoji} Resultado: {resultado}\n"
            f"{emoji} Error: NINGUNO\n"
            "----------------------------\n"
            f"{emoji} Marca: {marca}\n"
            f"{emoji} Tipo de tarjeta: {tipo_tarjeta}\n"
            f"{emoji} Nivel de tarjeta: {nivel_tarjeta}\n"
            f"{emoji} Banco: {banco}\n"
            f"{emoji} Teléfono: {telefono}\n"
            f"{emoji} País: {pais}\n"
            "----------------------------\n"
            f"{emoji} Desarrollado por:\n"
            f"{emoji} {dev_code}\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Enviar el resultado con el botón
        sent_message = await original_update.message.reply_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Live", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, sent_message.chat_id, sent_message.message_id, 30))
    
    except Exception as e:
        await original_update.message.reply_text(f"Error procesando tarjeta {index+1}: {e}")
        


# Función para Atlantic single
@require_key
@require_plan(['OLIMPO', '1MES', '15DIAS'])
async def atlantic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # VERIFICAR CRÉDITOS DISPONIBLES (mínimo 1 para dead)
        user_credits = await get_cookie_user(user_id)
        if user_credits['credits'] < 1:
            await update.message.reply_text(
                "❌ *Créditos insuficientes*\n\n"
                "Necesitas al menos 1 crédito para usar este gate.\n"
                f"💰 Tus créditos: {user_credits['credits']}\n\n"
                "Contacta a un admin para recargar.",
                parse_mode='Markdown'
            )
            return
        
        # Cooldown
        now = datetime.now()
        if user_id in last_at_time:
            time_diff = (now - last_at_time[user_id]).total_seconds()
            if time_diff < AT_COOLDOWN:
                remaining_time = AT_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /at."
                )
                return
        
        # Verificar argumentos
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/at <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/at 4532640527811643|12|2025|123"
            )
            return
        
        card_info = context.args[0]
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text("❌ Formato incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>")
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener info del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Atlantida...\n"
            f"💰 Créditos disponibles: {user_credits['credits']}\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Llamar a la API de Atlantic
        result = await atlantic_check(card_info, user_id)
        # SANITIZAR el mensaje de error si existe
        if "message" in result:
            result["message"] = sanitize_gate_response(result["message"])
        
        # DETERMINAR COSTO SEGÚN RESPUESTA
        if result["status"] == "✅ Live":
            cost = 2
        elif result["status"] == "❌ Dead":
            cost = 1
        else:
            cost = 0
        
        # CONSUMIR CRÉDITOS SI ES NECESARIO
        if cost > 0:
            success = await use_cookie_credits(user_id, cost)
            if not success:
                await processing_message.edit_text(
                    "❌ *Error al consumir créditos*\n\n"
                    "No se pudieron deducir los créditos de tu cuenta.\n"
                    "Verifica tu saldo con /usocreditos"
                )
                return
        
        # Determinar resultado para el mensaje
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🌊 Atlantida Check\n" 
            "🌊 Comando: /at\n"
            "----------------------------\n"
            f"🌊 {card_code}\n"
            f"🌊 Status: {status_icon}\n"
            f"🌊 Response: {result['message']}\n"
            f"🌊 Resultado: {resultado}\n"
            f"🌊 Costo: {cost} crédito(s)\n"
            "----------------------------\n"
            f"🌊 Marca: {marca}\n"
            f"🌊 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🌊 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🌊 Banco: {banco}\n"
            f"🌊 Teléfono: {telefono}\n"
            f"🌊 País: {pais}\n"
            "----------------------------\n"
            "🌊 Desarrollado por:\n"
            f"🌊 {dev_code}\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        last_at_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para Atlantic mass
@require_key
@require_plan(['OLIMPO', '1MES', '15DIAS'])
async def atlantic_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # VERIFICAR CRÉDITOS DISPONIBLES (mínimo 1 para empezar)
        user_credits = await get_cookie_user(user_id)
        if user_credits['credits'] < 1:
            await update.message.reply_text(
                "❌ *Créditos insuficientes*\n\n"
                "Necesitas al menos 1 crédito para usar este gate.\n"
                f"💰 Tus créditos: {user_credits['credits']}\n\n"
                "Contacta a un admin para recargar.",
                parse_mode='Markdown'
            )
            return
        
        now = datetime.now()
        if user_id in last_atmass_time:
            time_diff = (now - last_atmass_time[user_id]).total_seconds()
            if time_diff < ATMASS_COOLDOWN:
                remaining_time = ATMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /atmass."
                )
                return
        
        lines = []
        
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                parts = [p.strip() for p in line.split('|')]
                
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10]

        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /atmass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo: 4532640527811643|12|2025|123"
            )
            return

        total_cards = len(lines)
        
        # Enviar mensaje inicial de que comenzó el proceso
        status_message = await update.message.reply_text(
            f"🔄 Iniciando verificación de {total_cards} tarjetas con Atlantida...\n"
            f"💰 Créditos disponibles: {user_credits['credits']}\n"
            f"Enviando resultados en tiempo real ⏳"
        )
        
        last_atmass_time[user_id] = now
        
        # Procesar UNA POR UNA y enviar resultado inmediatamente
        for index, card_data in enumerate(lines):
            try:
                # Verificar si aún tiene créditos antes de cada petición
                current_credits = await get_cookie_user(user_id)
                if current_credits['credits'] < 1:
                    await update.message.reply_text(
                        "❌ *Créditos agotados*\n\n"
                        "No tienes créditos suficientes para continuar.\n"
                        f"💰 Créditos restantes: {current_credits['credits']}",
                        parse_mode='Markdown'
                    )
                    break
                
                # Procesar tarjeta individual (llama a la API de Atlantic)
                result = await atlantic_check(card_data, user_id)
                
                # SANITIZAR el mensaje de error si existe (AQUÍ SÍ VA)
                if "message" in result:
                    result["message"] = sanitize_gate_response(result["message"])
                
                # DETERMINAR COSTO SEGÚN RESPUESTA
                if result["status"] == "✅ Live":
                    cost = 2
                elif result["status"] == "❌ Dead":
                    cost = 1
                else:
                    cost = 0
                
                # CONSUMIR CRÉDITOS SI ES NECESARIO
                if cost > 0:
                    success = await use_cookie_credits(user_id, cost)
                    if not success:
                        await update.message.reply_text(
                            f"⚠️ *Créditos insuficientes* para la tarjeta {index + 1}\n"
                            f"Costo requerido: {cost} crédito(s)\n"
                            f"Se detiene el proceso masivo.",
                            parse_mode='Markdown'
                        )
                        break
                
                # Enviar resultado INMEDIATAMENTE (no esperar a las demás)
                await send_gate_result(update, context, index, result, card_data, user_id, update, "Atlantida")
                
                # Actualizar mensaje de estado cada 2 tarjetas
                if (index + 1) % 2 == 0 or (index + 1) == total_cards:
                    try:
                        remaining_credits = (await get_cookie_user(user_id))['credits']
                        await status_message.edit_text(
                            f"🔄 Procesando tarjetas con Atlantida...\n"
                            f"✅ Completadas: {index + 1}/{total_cards}\n"
                            f"⏳ Pendientes: {total_cards - (index + 1)}\n"
                            f"💰 Créditos restantes: {remaining_credits}"
                        )
                    except:
                        pass
                
                # Pequeña pausa para no saturar Telegram (0.3 segundos)
                await asyncio.sleep(0.3)
                
            except Exception as e:
                # Si falla una tarjeta, continuar con la siguiente
                print(f"❌ Error tarjeta {index + 1}: {e}")
                continue
        
        # Mensaje final cuando termina todo
        try:
            final_credits = (await get_cookie_user(user_id))['credits']
            await status_message.edit_text(
                f"✅ Verificación completada\n"
                f"📝 Total: {total_cards} tarjetas procesadas\n"
                f"💰 Créditos restantes: {final_credits}"
            )
        except:
            pass
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")



# Función para el comando /di (Euridice)
@require_key
@require_plan(['OLIMPO', '1MES', '15DIAS'])
async def euridice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        
        # Verificar si ha pasado el tiempo mínimo desde la última ejecución de /di
        now = datetime.now()
        if user_id in last_di_time:
            time_diff = (now - last_di_time[user_id]).total_seconds()
            if time_diff < DI_COOLDOWN:
                remaining_time = DI_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /di."
                )
                return
        
        # Verificar si se proporcionaron datos de la tarjeta
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/di <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/di 4532640527811643|12|2025|123"
            )
            return
        
        # Extraer datos de la tarjeta
        card_info = context.args[0]
        
        # Validar formato de la tarjeta
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Enviar mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Euridice...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Realizar la verificación
        result = await gate_check("Euridice", card_info, user_id)
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje de respuesta
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Euridice Check\n"
            "🔥 Comando: /di\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Editar el mensaje de procesamiento con el resultado
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Live", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        # Actualizar el tiempo de la última ejecución de /di
        last_di_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /dimass (Euridice masivo)
@require_key
@require_plan(['OLIMPO', '1MES', '15DIAS'])
async def euridice_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Control de tiempo
        now = datetime.now()
        if user_id in last_dimass_time:
            time_diff = (now - last_dimass_time[user_id]).total_seconds()
            if time_diff < DIMASS_COOLDOWN:
                remaining_time = DIMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /dimass."
                )
                return
        
        lines = []
        
        # Función auxiliar para limpiar y extraer tarjetas
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            # Limpiar HTML y emojis
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                # Separar por |
                parts = [p.strip() for p in line.split('|')]
                
                # Debe tener exactamente 4 partes
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                # Validar que mes y año sean números
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                # Validar que el CVV tenga 3 o 4 dígitos (o sea rnd/xxx)
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                # Validar cantidad de dígitos: 15 o 16
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        # 1. Intentar extraer del mensaje respondido
        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10] # Limitar a 10

        # 2. Fallback: Intentar extraer del mensaje actual
        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /dimass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
                "Ejemplo VISA: 4532640527811643|12|2025|123"
            )
            return

        # Mostrar procesamiento
        processed_count = 0
        total_cards = len(lines)
        
        processing_message = await update.message.reply_text(
            "⏳ Procesando tarjetas con Euridice por favor espere...\n"
            f"Procesadas: 0/{total_cards}"
        )
        
        current_processing_message = processing_message
        
        async def result_callback(index, result, card_data, user_id):
            nonlocal processed_count, current_processing_message
            await send_gate_result(update, context, index, result, card_data, user_id, update, "Euridice")
            processed_count += 1
            
            if processed_count < total_cards:
                new_msg = await update.message.reply_text(
                    "⏳ Procesando tarjetas con Euridice por favor espere...\n"
                    f"Procesadas: {processed_count}/{total_cards}"
                )
                try:
                    await current_processing_message.delete()
                except:
                    pass
                current_processing_message = new_msg
            else:
                try:
                    await current_processing_message.delete()
                except:
                    pass
        
        last_dimass_time[user_id] = now
        asyncio.create_task(gate_check_multiple("Euridice", lines, user_id, result_callback))
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /ph (Philotes)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def philotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        
        # Verificar si ha pasado el tiempo mínimo desde la última ejecución de /ph
        now = datetime.now()
        if user_id in last_ph_time:
            time_diff = (now - last_ph_time[user_id]).total_seconds()
            if time_diff < PH_COOLDOWN:
                remaining_time = PH_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /ph."
                )
                return
        
        # Verificar si se proporcionaron datos de la tarjeta
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/ph <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/ph 4532640527811643|12|2025|123"
            )
            return
        
        # Extraer datos de la tarjeta
        card_info = context.args[0]
        
        # Validar formato de la tarjeta
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Enviar mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Philotes...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Realizar la verificación
        result = await gate_check("Philotes", card_info, user_id)
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje de respuesta
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Philotes Check\n"
            "🔥 Comando: /ph\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Editar el mensaje de procesamiento con el resultado
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Live", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        # Actualizar el tiempo de la última ejecución de /ph
        last_ph_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /phmass (Philotes masivo)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def philotes_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Control de tiempo
        now = datetime.now()
        if user_id in last_phmass_time:
            time_diff = (now - last_phmass_time[user_id]).total_seconds()
            if time_diff < PHMASS_COOLDOWN:
                remaining_time = PHMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /phmass."
                )
                return
        
        lines = []
        
        # Función auxiliar para limpiar y extraer tarjetas
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            # Limpiar HTML y emojis
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                # Separar por |
                parts = [p.strip() for p in line.split('|')]
                
                # Debe tener exactamente 4 partes
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                # Validar que mes y año sean números
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                # Validar que el CVV tenga 3 o 4 dígitos (o sea rnd/xxx)
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                # Validar cantidad de dígitos: 15 o 16
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        # 1. Intentar extraer del mensaje respondido
        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10] # Limitar a 10

        # 2. Fallback: Intentar extraer del mensaje actual
        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /phmass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
                "Ejemplo VISA: 4532640527811643|12|2025|123"
            )
            return

        # Mostrar procesamiento
        processed_count = 0
        total_cards = len(lines)
        
        processing_message = await update.message.reply_text(
            "⏳ Procesando tarjetas con Philotes por favor espere...\n"
            f"Procesadas: 0/{total_cards}"
        )
        
        current_processing_message = processing_message
        
        async def result_callback(index, result, card_data, user_id):
            nonlocal processed_count, current_processing_message
            await send_gate_result(update, context, index, result, card_data, user_id, update, "Philotes")
            processed_count += 1
            
            if processed_count < total_cards:
                new_msg = await update.message.reply_text(
                    "⏳ Procesando tarjetas con Philotes por favor espere...\n"
                    f"Procesadas: {processed_count}/{total_cards}"
                )
                try:
                    await current_processing_message.delete()
                except:
                    pass
                current_processing_message = new_msg
            else:
                try:
                    await current_processing_message.delete()
                except:
                    pass
        
        last_phmass_time[user_id] = now
        asyncio.create_task(gate_check_multiple("Philotes", lines, user_id, result_callback))
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /eu (Eurinias)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def eurinias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        
        # Verificar si ha pasado el tiempo mínimo desde la última ejecución de /eu
        now = datetime.now()
        if user_id in last_eu_time:
            time_diff = (now - last_eu_time[user_id]).total_seconds()
            if time_diff < EU_COOLDOWN:
                remaining_time = EU_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /eu."
                )
                return
        
        # Verificar si se proporcionaron datos de la tarjeta
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/eu <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/eu 4532640527811643|12|2025|123"
            )
            return
        
        # Extraer datos de la tarjeta
        card_info = context.args[0]
        
        # Validar formato de la tarjeta
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Enviar mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Eurinias...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Realizar la verificación
        result = await gate_check("Eurinias", card_info, user_id)
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje de respuesta
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Eurinias Check\n"
            "🔥 Comando: /eu\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Editar el mensaje de procesamiento con el resultado
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Live", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        # Actualizar el tiempo de la última ejecución de /eu
        last_eu_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /eumass (Eurinias masivo)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def eurinias_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Control de tiempo
        now = datetime.now()
        if user_id in last_eumass_time:
            time_diff = (now - last_eumass_time[user_id]).total_seconds()
            if time_diff < EUMASS_COOLDOWN:
                remaining_time = EUMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /eumass."
                )
                return
        
        lines = []
        
        # Función auxiliar para limpiar y extraer tarjetas
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            # Limpiar HTML y emojis
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                # Separar por |
                parts = [p.strip() for p in line.split('|')]
                
                # Debe tener exactamente 4 partes
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                # Validar que mes y año sean números
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                # Validar que el CVV tenga 3 o 4 dígitos (o sea rnd/xxx)
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                # Validar cantidad de dígitos: 15 o 16
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        # 1. Intentar extraer del mensaje respondido
        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10] # Limitar a 10

        # 2. Fallback: Intentar extraer del mensaje actual
        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /eumass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
                "Ejemplo VISA: 4532640527811643|12|2025|123"
            )
            return

        # Mostrar procesamiento
        processed_count = 0
        total_cards = len(lines)
        
        processing_message = await update.message.reply_text(
            "⏳ Procesando tarjetas con Eurinias por favor espere...\n"
            f"Procesadas: 0/{total_cards}"
        )
        
        current_processing_message = processing_message
        
        async def result_callback(index, result, card_data, user_id):
            nonlocal processed_count, current_processing_message
            await send_gate_result(update, context, index, result, card_data, user_id, update, "Eurinias")
            processed_count += 1
            
            if processed_count < total_cards:
                new_msg = await update.message.reply_text(
                    "⏳ Procesando tarjetas con Eurinias por favor espere...\n"
                    f"Procesadas: {processed_count}/{total_cards}"
                )
                try:
                    await current_processing_message.delete()
                except:
                    pass
                current_processing_message = new_msg
            else:
                try:
                    await current_processing_message.delete()
                except:
                    pass
        
        last_eumass_time[user_id] = now
        asyncio.create_task(gate_check_multiple("Eurinias", lines, user_id, result_callback))
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /wo (Wojtek)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def wojtek_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Obtener el ID de usuario
        user_id = update.effective_user.id
        
        # Verificar si ha pasado el tiempo mínimo desde la última ejecución de /wo
        now = datetime.now()
        if user_id in last_wo_time:
            time_diff = (now - last_wo_time[user_id]).total_seconds()
            if time_diff < WO_COOLDOWN:
                remaining_time = WO_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /wo."
                )
                return
        
        # Verificar si se proporcionaron datos de la tarjeta
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/wo <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/wo 4532640527811643|12|2025|123"
            )
            return
        
        # Extraer datos de la tarjeta
        card_info = context.args[0]
        
        # Validar formato de la tarjeta
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        # Obtener información del BIN
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        # Enviar mensaje de procesamiento
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Wojtek...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Realizar la verificación
        result = await gate_check("Wojtek", card_info, user_id)
        
        # Determinar el resultado
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        # Construir mensaje de respuesta
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        dev_code = f"<code>@Chack0071</code> <a href='tg://copy?text=@Chack0071'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "🔥 Wojtek Check\n"
            "🔥 Comando: /wo\n"
            "----------------------------\n"
            f"🔥 {card_code}\n"
            f"🔥 Status: {status_icon}\n"
            f"🔥 Response: {result['message']}\n"
            f"🔥 Resultado: {resultado}\n"
            f"🔥 Error: NINGUNO\n"
            "----------------------------\n"
            f"🔥 Marca: {marca}\n"
            f"🔥 Tipo de tarjeta: {tipo_tarjeta}\n"
            f"🔥 Nivel de tarjeta: {nivel_tarjeta}\n"
            f"🔥 Banco: {banco}\n"
            f"🔥 Teléfono: {telefono}\n"
            f"🔥 País: {pais}\n"
            "----------------------------\n"
            "🔥 <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        # Crear el botón para ir al canal de Telegram
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Editar el mensaje de procesamiento con el resultado
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        # Si el resultado no es "✅ Live", programar la eliminación del mensaje después de 30 segundos
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        # Actualizar el tiempo de la última ejecución de /wo
        last_wo_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# Función para el comando /womass (Wojtek masivo)
@require_key
@require_plan(['OLIMPO', '1MES'])
async def wojtek_mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Control de tiempo
        now = datetime.now()
        if user_id in last_womass_time:
            time_diff = (now - last_womass_time[user_id]).total_seconds()
            if time_diff < WOMASS_COOLDOWN:
                remaining_time = WOMASS_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /womass."
                )
                return
        
        lines = []
        
        # Función auxiliar para limpiar y extraer tarjetas
        def extract_cards_from_text(text):
            cards = []
            if not text:
                return cards
            
            # Limpiar HTML y emojis
            import re
            text_clean = re.sub(r'<[^>]+>', '', text)
            text_clean = re.sub(r'[🔥🏛️🔹•-]', '', text_clean)
            lines_text = text_clean.split('\n')
            
            for line in lines_text:
                line = line.strip()
                if not line:
                    continue
                
                # Separar por |
                parts = [p.strip() for p in line.split('|')]
                
                # Debe tener exactamente 4 partes
                if len(parts) != 4:
                    continue
                
                cc, mes, ano, cvv = parts
                
                # Validar que mes y año sean números
                if not mes.isdigit() or not ano.isdigit():
                    continue
                
                # Validar que el CVV tenga 3 o 4 dígitos (o sea rnd/xxx)
                if cvv not in ('rnd', 'xxx', 'xxxx'):
                    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                        continue
                
                # Validar cantidad de dígitos: 15 o 16
                if len(cc) not in (15, 16):
                    continue
                
                if 'x' in cc:
                    continue 
                    
                cards.append(f"{cc}|{mes}|{ano}|{cvv}")
            
            return cards

        # 1. Intentar extraer del mensaje respondido
        if update.message.reply_to_message:
            replied_text = update.message.reply_to_message.text
            extracted = extract_cards_from_text(replied_text)
            if extracted:
                lines = extracted[:10] # Limitar a 10

        # 2. Fallback: Intentar extraer del mensaje actual
        if not lines:
            message_text = update.message.text
            text_parts = message_text.split('\n')
            if len(text_parts) > 1:
                text_content = '\n'.join(text_parts[1:])
                extracted = extract_cards_from_text(text_content)
                if extracted:
                    lines = extracted[:10]

        if not lines:
            await update.message.reply_text(
                "❌ No se encontraron tarjetas para verificar.\n"
                "Responde al mensaje de /gen con /womass o escribe las tarjetas.\n\n"
                "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
                "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
                "Ejemplo VISA: 4532640527811643|12|2025|123"
            )
            return

        # Mostrar procesamiento
        processed_count = 0
        total_cards = len(lines)
        
        processing_message = await update.message.reply_text(
            "⏳ Procesando tarjetas con Wojtek por favor espere...\n"
            f"Procesadas: 0/{total_cards}"
        )
        
        current_processing_message = processing_message
        
        async def result_callback(index, result, card_data, user_id):
            nonlocal processed_count, current_processing_message
            await send_gate_result(update, context, index, result, card_data, user_id, update, "Wojtek")
            processed_count += 1
            
            if processed_count < total_cards:
                new_msg = await update.message.reply_text(
                    "⏳ Procesando tarjetas con Wojtek por favor espere...\n"
                    f"Procesadas: {processed_count}/{total_cards}"
                )
                try:
                    await current_processing_message.delete()
                except:
                    pass
                current_processing_message = new_msg
            else:
                try:
                    await current_processing_message.delete()
                except:
                    pass
        
        last_womass_time[user_id] = now
        asyncio.create_task(gate_check_multiple("Wojtek", lines, user_id, result_callback))
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")



# === INICIO LÓGICA GENERACIÓN COOKIES ===
async def generate_cookie_task(user_id, cookie_type, context, chat_id, message_id):
    """Tarea asíncrona para generar cookies - Versión corregida"""
    task_id = None
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-API-Key": COOKIE_BOT_API_KEY}
            
            # Crear tarea
            try:
                async with session.post(
                    f"{COOKIE_API_URL}/cookie/generate", 
                    headers=headers, 
                    json={
                        "cookie_type": cookie_type,
                        "target_user_id": user_id
                    }
                ) as r:
                    if r.status != 200:
                        raise Exception(f"Error HTTP {r.status} al crear tarea")
                    data = await r.json()
                    task_id = data["task_id"]
            except Exception as e:
                # Fallo al crear la tarea - aquí SÍ reembolsamos
                raise Exception(f"No se pudo crear la tarea: {e}")

            # Polling cada 5 segundos, máximo 30 minutos (360 ciclos)
            max_attempts = 360  # 30 minutos de espera
            attempt = 0
            
            while attempt < max_attempts:
                try:
                    await asyncio.sleep(5)
                    attempt += 1
                    
                    async with session.get(
                        f"{COOKIE_API_URL}/cookie/status/{task_id}", 
                        headers=headers
                    ) as sr:
                        if sr.status != 200:
                            print(f"Error HTTP {sr.status} en status check")
                            continue
                            
                        resp = await sr.json()
                        status = resp.get("status")
                        
                        if status == "completed":
                            # Éxito: La cookie fue enviada directamente por la API
                            await context.bot.edit_message_text(
                                chat_id=chat_id, 
                                message_id=message_id, 
                                text="✅ ¡Cookie generada exitosamente! Revisa tus mensajes privados."
                            )
                            return
                        
                        elif status == "failed":
                            # Fallo confirmado por la API - aquí reembolsamos
                            error_msg = resp.get("error", "Fallo desconocido")
                            await add_cookie_credits(user_id, COOKIE_CREDITS_PER_COOKIE, "system")
                            
                            await context.bot.send_message(
                                chat_id=user_id, 
                                text=f"❌ La API confirmó el fallo: {error_msg}\n\n"
                                     f"💰 {COOKIE_CREDITS_PER_COOKIE} créditos han sido reembolsados."
                            )
                            await context.bot.edit_message_text(
                                chat_id=chat_id, 
                                message_id=message_id, 
                                text="❌ Falló la generación. Créditos reembolsados."
                            )
                            return
                        
                        # Si es "processing" u otro estado, continuamos esperando
                        
                except aiohttp.ClientError as e:
                    # Error de red temporal - NO reembolsar, solo loguear
                    print(f"Error de red en intento {attempt}: {e}")
                    continue
                    
                except Exception as e:
                    print(f"Error inesperado en polling: {e}")
                    continue
            
            # Si llegamos aquí: pasaron 30 minutos sin respuesta definitiva
            # NO reembolsamos - la API podría estar terminando
            await context.bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text=f"⏳ *Generación en proceso prolongado*\n\n"
                     f"La API está tardando más de 30 minutos.\n"
                     f"🔄 Tu solicitud sigue activa en el servidor.\n\n"
                     f"💡 *No se han reembolsado los créditos todavía.*\n"
                     f"Si no recibes la cookie en 15 minutos más, contacta a soporte.\n\n"
                     f"🆔 Task ID: `{task_id}`",
                parse_mode='Markdown'
            )
            
            # Opcional: Guardar en una tabla de "tareas pendientes" para revisión manual
            
    except Exception as e:
        print(f"Error crítico en cookie: {e}")
        
        # Solo reembolsar si falló la creación inicial (sin task_id)
        if task_id is None:
            await add_cookie_credits(user_id, COOKIE_CREDITS_PER_COOKIE, "system")
            try:
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=f"❌ Error al iniciar: {str(e)[:100]}\n\n"
                         f"💰 {COOKIE_CREDITS_PER_COOKIE} créditos reembolsados."
                )
                await context.bot.edit_message_text(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    text="❌ No se pudo iniciar la generación. Créditos reembolsados."
                )
            except: 
                pass
        else:
            # Si ya tenemos task_id, no sabemos el estado real
            # NO reembolsamos automáticamente
            try:
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=f"⚠️ Error de conexión detectado, pero tu solicitud (Task ID: `{task_id}`) "
                         f"podría estar completándose.\n\n"
                         f"⏳ Espera 5 minutos más. Si no recibes nada, contacta a soporte.\n"
                         f"💰 Los créditos se mantienen retenidos hasta confirmar el estado.",
                    parse_mode='Markdown'
                )
            except:
                pass
                
    finally:
        async with cookie_sessions_lock:
            if user_id in cookie_active_sessions: 
                del cookie_active_sessions[user_id]
            if cookie_waiting_queue and len(cookie_active_sessions) < COOKIE_MAX_CONCURRENT:
                next_uid = cookie_waiting_queue.pop(0)
                try: 
                    await context.bot.send_message(
                        chat_id=next_uid, 
                        text="🔔 ¡Es tu turno! Pulsa '🍪 Generar Cookie' de nuevo."
                    )
                except: 
                    pass
# === FIN LÓGICA GENERACIÓN COOKIES ===


# === INICIO COMANDOS Y HANDLERS COOKIES ===
async def cookie_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cookie - Inicia el menú de generación de cookies"""
    user = update.effective_user
    user_info = await get_cookie_user(user.id)
    
    kb = [
        [InlineKeyboardButton("🍪 Generar Cookie", callback_data="cookie:gencookie")],
        [InlineKeyboardButton("💰 Mis Créditos", callback_data="cookie:mycredits")]
    ]
    if await is_cookie_admin(user.id):
        kb.append([InlineKeyboardButton("🔧 Panel Admin Cookies", callback_data="cookie:adminpanel")])

    text = (
        f"🔥 *HadesCookie* 🔥\n\n"
        f"👤 Usuario: {user.first_name}\n"
        f"💰 Créditos: {user_info['credits']}\n"
        f"🍪 Costo por cookie: {COOKIE_CREDITS_PER_COOKIE}\n\n"
        f"Selecciona una opción:"
    )
    
    # Detectar si viene de un botón (callback) o de comando directo
    if update.callback_query:
        # Viene del botón del menú principal - editar mensaje existente
        await update.callback_query.edit_message_text(
            text, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        # Viene del comando /cookie directo
        await update.message.reply_text(
            text, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def cookie_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cookiestatus - Ver estado del sistema"""
    async with cookie_sessions_lock:
        active = len(cookie_active_sessions)
        queue = len(cookie_waiting_queue)
    await update.message.reply_text(
        f"📊 *Estado HadesCookie*\n\n"
        f"🔄 Sesiones activas: {active}/{COOKIE_MAX_CONCURRENT}\n"
        f"⏳ En cola: {queue}\n"
        f"💰 Costo: {COOKIE_CREDITS_PER_COOKIE} créditos"
    )

async def cookie_addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /addcookiecredits - Solo admins"""
    if not await is_cookie_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo admins.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Uso: `/addcookiecredits [user_id] [cantidad]`")
        return
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
        await add_cookie_credits(uid, amount, update.effective_user.id)
        
        # REGISTRAR EN AUDITORÍA
        await log_admin_action(
            admin_id=update.effective_user.id,
            action_type="ADD_COOKIE_CREDITS",
            target_id=uid,
            details=f"Agregó {amount} créditos de cookies"
        )
        
        await update.message.reply_text(f"✅ {amount} créditos añadidos a {uid}.")
    except ValueError:
        await update.message.reply_text("❌ IDs y cantidades deben ser números.")

async def cookie_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para botones de cookies"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "cookie:mycredits":
        info = await get_cookie_user(user_id)
        await query.edit_message_text(
            f"💰 *Tus Créditos*\n\n"
            f"👤 {info['first_name']}\n"
            f"💳 Disponibles: {info['credits']}\n"
            f"🍪 Costo: {COOKIE_CREDITS_PER_COOKIE}\n"
            f"📅 Última actividad: {info['last_used']}"
        )

    elif data == "cookie:gencookie":
        info = await get_cookie_user(user_id)
        if info["credits"] < COOKIE_CREDITS_PER_COOKIE:
            await query.edit_message_text(
                f"❌ *Créditos insuficientes*\n\n"
                f"💰 Tienes: {info['credits']}\n"
                f"🍪 Necesitas: {COOKIE_CREDITS_PER_COOKIE}\n\n"
                f"Contacta a un admin para recargar."
            )
            return
        
        kb = [
            [InlineKeyboardButton("🇲🇽 MX", callback_data="cookie:type:MX")], 
            [InlineKeyboardButton("🇺🇸 US", callback_data="cookie:type:US")]
        ]
        await query.edit_message_text(
            "🍪 *Selecciona tipo de cookie:*", 
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data.startswith("cookie:type:"):
        ctype = data.split(":")[2].upper()
        
        # Verificar créditos de nuevo
        info = await get_cookie_user(user_id)
        if info["credits"] < COOKIE_CREDITS_PER_COOKIE:
            await query.edit_message_text("❌ Créditos insuficientes.")
            return

        async with cookie_sessions_lock:
            if user_id in cookie_active_sessions:
                await query.edit_message_text("⏳ Ya tienes una cookie en proceso.")
                return
            if len(cookie_active_sessions) >= COOKIE_MAX_CONCURRENT:
                cookie_waiting_queue.append(user_id)
                pos = len(cookie_waiting_queue)
                await query.edit_message_text(
                    f"🔄 *Servidor lleno*\n\n"
                    f"📝 Posición en cola: #{pos}\n"
                    f"Espera tu turno..."
                )
                return
            cookie_active_sessions[user_id] = {"status": "processing", "type": ctype}

        if not await use_cookie_credits(user_id, COOKIE_CREDITS_PER_COOKIE):
            await query.edit_message_text("❌ Error al descontar créditos.")
            async with cookie_sessions_lock:
                if user_id in cookie_active_sessions: 
                    del cookie_active_sessions[user_id]
            return

        await query.edit_message_text(
            f"🔄 *Generando cookie {ctype}...*\n"
            f"💰 -{COOKIE_CREDITS_PER_COOKIE} créditos\n"
            f"⏳ Esto puede tardar unos minutos."
        )
        
        # Iniciar tarea en background
        asyncio.create_task(
            generate_cookie_task(user_id, ctype, context, query.message.chat_id, query.message.message_id)
        )

    elif data == "cookie:adminpanel" and await is_cookie_admin(user_id):
        kb = [
            [InlineKeyboardButton("➕ Agregar Créditos", callback_data="cookie:addcredits")],
            [InlineKeyboardButton("🔙 Volver", callback_data="cookie:back")]
        ]
        await query.edit_message_text(
            "🔧 *Panel Admin Cookies*\n\n"
            "Usa `/addcookiecredits [id] [cantidad]` para agregar créditos.", 
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data == "cookie:addcredits":
        await query.edit_message_text(
            "💡 Usa el comando:\n"
            "`/addcookiecredits [user_id] [cantidad]`\n"
            "Ejemplo: `/addcookiecredits 123456 100`"
        )

    elif data == "cookie:back":
        # Volver al menú principal de cookies
        await cookie_start(update, context)
# === FIN COMANDOS Y HANDLERS COOKIES ===

# === INICIO COMANDO CRONOS PAYPAL $0.10 USD - SINGLE ONLY CON ANTISPAM 30s ===
@require_key
@require_plan(['OLIMPO', '1MES'])
async def cronos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        # Antispam 30 segundos entre checks
        now = datetime.now()
        if user_id in last_cr_time:
            time_diff = (now - last_cr_time[user_id]).total_seconds()
            if time_diff < CR_COOLDOWN:
                remaining_time = CR_COOLDOWN - time_diff
                await update.message.reply_text(
                    f"⏳ Debes esperar {remaining_time:.1f} segundos antes de volver a usar /cr.\n"
                    f"⏳ Cronos es single check - sin mass."
                )
                return
        
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Formato incorrecto. Usa:\n"
                "/cr <cc>|<mes>|<ano>|<cvv>\n\n"
                "Ejemplo:\n"
                "/cr 4532640527811643|12|2025|123\n\n"
                "⏳ Cronos Paypal $0.10 USD - Solo 1 tarjeta por vez (antispam 30s)"
            )
            return
        
        card_info = context.args[0]
        
        card_parts = card_info.split("|")
        if len(card_parts) != 4:
            await update.message.reply_text(
                "❌ Formato de tarjeta incorrecto. Usa: <cc>|<mes>|<ano>|<cvv>"
            )
            return
        
        cc, mes, ano, cvv = card_parts
        
        bin_info = get_card_info(cc[:6])
        marca = bin_info['Marca'] if bin_info else "Desconocido"
        tipo_tarjeta = bin_info['Tipo de tarjeta'] if bin_info else "Desconocido"
        nivel_tarjeta = bin_info['Nivel de tarjeta'] if bin_info else "Desconocido"
        banco = bin_info['Banco'] if bin_info else "Desconocido"
        telefono = bin_info['Teléfono'] if bin_info else "Desconocido"
        pais = bin_info['País'] if bin_info else "Desconocido"
        
        processing_message = await update.message.reply_text(
            f"⏳ Procesando verificación con Cronos PayPal $0.10 USD...\n"
            f"Peticiones globales: {sum(user_request_count.values())}\n"
            f"Tus peticiones: {user_request_count[user_id]}/{MAX_USER_REQUESTS}"
        )
        
        # Llamada optimizada via paypal_optimized.py (direct + cloudscraper, sin proxy)
        result = await paypal_gate_check(card_info, user_id)
        # Sanitizar respuesta
        if "message" in result:
            result["message"] = sanitize_gate_response(result["message"])
        
        status_raw = result["status"]
        if status_raw == "✅ Live":
            status_icon = "✅ Live"
            resultado = "APROBADA"
        elif status_raw == "❌ Dead":
            status_icon = "❌ Dead"
            resultado = "DECLINADA"
        else:
            status_icon = status_raw
            resultado = "ERROR"
        
        card_code = f"<code>{card_info}</code> <a href='tg://copy?text={card_info}'></a>"
        
        response_message = (
            "----------------------------\n"
            "🏛️Hades V1🏛️\n"
            "----------------------------\n"
            "⏳ Cronos PayPal $0.10 USD\n"
            "⏳ Comando: /cr\n"
            "----------------------------\n"
            f"⏳ {card_code}\n"
            f"⏳ Status: {status_icon}\n"
            f"⏳ Response: {result['message']}\n"
            f"⏳ Resultado: {resultado}\n"
            f"⏳ Error: NINGUNO\n"
            "----------------------------\n"
            f"⏳ Marca: {marca}\n"
            f"⏳ Tipo de tarjeta: {tipo_tarjeta}\n"
            f"⏳ Nivel de tarjeta: {nivel_tarjeta}\n"
            f"⏳ Banco: {banco}\n"
            f"⏳ Teléfono: {telefono}\n"
            f"⏳ País: {pais}\n"
            "----------------------------\n"
            "⏳ <b>Developer:</b> @Chack0071\n"
            "----------------------------\n"
            "🏛️ <b>OLIMPO CHK</b> 🏛️\n"
            "----------------------------\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("OLIMPO BINS Referencias", url="https://t.me/+c3vmo7KwhLAyZjE5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        result_message = await processing_message.edit_text(response_message, reply_markup=reply_markup, parse_mode="HTML")
        
        if status_raw != "✅ Live":
            asyncio.create_task(delete_message_after_delay(context, result_message.chat_id, result_message.message_id, 30))
        
        last_cr_time[user_id] = now
    
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# === FIN COMANDO CRONOS ===

# === INICIO COMANDO SECRETO CORTE ===
def escape_markdown(text: str) -> str:
    """Escapa caracteres especiales de Markdown"""
    if not text:
        return ""
    # Caracteres que necesitan escape en Markdown
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, f"\\{char}")
    return text

async def corte_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando secreto /corte - Solo Superadmin (5531198491)"""
    user_id = update.effective_user.id
    
    # Solo el creador/superadmin principal puede ver esto
    if user_id != COOKIE_SUPER_ADMIN:
        # Simular que el comando no existe
        return
    
    try:
        # Obtener últimos 50 movimientos
        logs = await get_admin_audit_log(limit=50)
        
        if not logs:
            await update.message.reply_text("📋 No hay movimientos registrados.")
            return
        
        # Construir mensaje
        response = "🔐 REGISTRO DE MOVIMIENTOS - ADMIN\n"
        response += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        response += "─" * 30 + "\n\n"
        
        for log in logs:
            admin_id = log['admin_id']
            action = log['action_type']
            target = log['target_id'] or "N/A"
            details = escape_markdown(log['details'])  # ESCAPAR DETALLES
            timestamp = log['timestamp'].strftime('%d/%m %H:%M')
            
            # Iconos según tipo
            icon = "📝"
            if "CREDITS" in action:
                icon = "💰"
            elif "GENKEY" in action:
                icon = "🔑"
            elif "EXTEND" in action:
                icon = "⏱"
            elif "ADD_ADMIN" in action:
                icon = "👮"
            elif "REVOKE" in action:
                icon = "🚫"
            
            response += f"{icon} {timestamp}\n"
            response += f"👤 Admin: {admin_id}\n"
            response += f"🎯 Target: {target}\n"
            response += f"⚡ Acción: {action}\n"
            response += f"📄 Detalles: {details}\n"
            response += "─" * 20 + "\n\n"
        
        response += "🔒 Fin del registro"
        
        # Enviar SIN parse_mode para evitar errores
        await update.message.reply_text(response)
        
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
# === FIN COMANDO SECRETO CORTE ===


# === INICIO COMANDO CORTETOTAL ===
async def corte_total_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando secreto /cortetotal - Exporta TODO el historial a CSV"""
    user_id = update.effective_user.id
    
    # Solo el superadmin principal
    if user_id != COOKIE_SUPER_ADMIN:
        return
    
    try:
        # Enviar mensaje de procesamiento
        processing_msg = await update.message.reply_text(
            "⏳ Generando archivo CSV con todo el historial...\n"
            "Esto puede tardar unos segundos."
        )
        
        # Generar archivo
        filename = f"corte_total_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath, message = await export_audit_log_to_csv(filename)
        
        if not filepath:
            await processing_msg.edit_text(f"❌ {message}")
            return
        
        # Obtener estadísticas
        total, admins = await get_audit_stats()
        
        # Enviar archivo
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(filepath, 'rb'),
            caption=(
                f"🔐 *CORTE TOTAL - AUDITORÍA*\n\n"
                f"📊 Total de movimientos: `{total}`\n"
                f"📁 Archivo: `{filename}`\n\n"
                f"👤 Top admins por movimientos:\n"
            ) + "\n".join([f"• `{a['admin_id']}`: {a['cantidad']} acciones" for a in admins[:5]]),
            parse_mode='Markdown'
        )
        
        # Eliminar mensaje de procesamiento
        await processing_msg.delete()
        
        # Limpiar archivo temporal
        import os
        os.remove(filepath)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error generando corte: {e}")
# === FIN COMANDO CORTETOTAL ===


# ============ FUNCIONES DE TECLADOS Y MENÚS ============

def get_main_menu_keyboard(user_id):
    """Teclado del menú principal"""
    keyboard = [
        [
            InlineKeyboardButton("🚪 Gates", callback_data="menu:gates:page:1"),
            InlineKeyboardButton("⚡ Generar", callback_data="menu:generar")
            
        ],
        [
            InlineKeyboardButton("👤 Perfil", callback_data="menu:perfil"),
            InlineKeyboardButton("📊 Stats", callback_data="menu:stats")
        ],
        [
            InlineKeyboardButton("❓ Ayuda", callback_data="menu:ayuda"),
            InlineKeyboardButton("⚙️ Config", callback_data="menu:config")
            
        ],
        [
            InlineKeyboardButton("🍪 Cookie Generator", callback_data="menu:cookie")
        ]
            
    ]
    
    
    # Si es admin, agregar botón de admin
    if key_manager.is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🔧 Panel Admin", callback_data="menu:admin")])
        
        
    
    return InlineKeyboardMarkup(keyboard)

def get_gates_paginated_keyboard(user_id, page=1):
    """Teclado de gates con paginación - TODOS los gates visibles"""
    # Obtener el plan del usuario (si no tiene, será None o '1SEMA' por defecto)
    user_plan = key_manager.get_user_plan(user_id)
    if not user_plan:
        user_plan = 'SIN_PLAN'  # Usuario sin plan activo
    
    # Obtener TODOS los gates (no filtrar)
    all_gates = list(GATES_CONFIG.items())
    
    total_gates = len(all_gates)
    total_pages = math.ceil(total_gates / GATES_PER_PAGE) if total_gates > 0 else 1
    
    # Calcular índices
    start_idx = (page - 1) * GATES_PER_PAGE
    end_idx = start_idx + GATES_PER_PAGE
    current_gates = all_gates[start_idx:end_idx]
    
    # Construir botones de gates
    keyboard = []
    for gate_key, gate_data in current_gates:
        status_emoji = "🟢" if gate_data['status'] == 'online' else "🔴"
        
        # Verificar si el usuario tiene acceso a este gate
        required_plans = gate_data.get('plan_required', [])
        has_access = user_plan in required_plans or key_manager.is_admin(user_id)
        
        # Agregar candado si no tiene acceso
        lock_emoji = "" if has_access else " 🔒"
        
        button_text = f"{gate_data['emoji']} {gate_data['name']} {status_emoji}{lock_emoji}"
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"gate:{gate_key}"
        )])
    
    # Botones de navegación
    nav_buttons = []
    
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Anterior", callback_data=f"menu:gates:page:{page-1}")
        )
    
    nav_buttons.append(
        InlineKeyboardButton("🏠 Menú", callback_data="menu:main")
    )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("➡️ Siguiente", callback_data=f"menu:gates:page:{page+1}")
        )
    
    keyboard.append(nav_buttons)
    
    # Indicador de página
    keyboard.append([InlineKeyboardButton(
        f"📄 Página {page} de {total_pages}", 
        callback_data="ignore"
    )])
    
    return InlineKeyboardMarkup(keyboard)

def get_generar_menu_keyboard():
    """Teclado del submenú Generar"""
    keyboard = [
        [InlineKeyboardButton("🎴 Generar CC", callback_data="generar:cc")],
        [InlineKeyboardButton("🔢 Buscar BIN", callback_data="generar:bin")],
        [InlineKeyboardButton("🧬 Extrapolar", callback_data="generar:extra")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_gate_action_keyboard(gate_key):
    """Teclado para acciones de un gate específico"""
    gate_data = GATES_CONFIG.get(gate_key, {})
    command = gate_data.get('command', gate_key)
    
    # Cronos es SINGLE ONLY (antispam 30s) - no exponer Mass
    if gate_key == "cronos":
        keyboard = [
            [InlineKeyboardButton("▶️ Verificar 1 CC", callback_data=f"gateaction:{gate_key}:single")],
            [InlineKeyboardButton("ℹ️ Ver Comando", callback_data=f"gateaction:{gate_key}:info")],
            [InlineKeyboardButton("⬅️ Volver a Gates", callback_data="menu:gates:page:1")]
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Verificar 1 CC", callback_data=f"gateaction:{gate_key}:single"),
                InlineKeyboardButton("▶️ Verificar Mass", callback_data=f"gateaction:{gate_key}:mass")
            ],
            [InlineKeyboardButton("ℹ️ Ver Comando", callback_data=f"gateaction:{gate_key}:info")],
            [InlineKeyboardButton("⬅️ Volver a Gates", callback_data="menu:gates:page:1")]
        ]
    return InlineKeyboardMarkup(keyboard)

# ============ HANDLERS DE CALLBACKS ============

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal para todos los botones"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    # Ignorar callbacks de "ignore"
    if data == "ignore":
        return
    
    # ========== MENÚ PRINCIPAL ==========
    if data == "menu:main":
        # Obtener créditos de cookies
        try:
            cookie_user = await get_cookie_user(user_id)
            credits = cookie_user.get('credits', 0)
        except:
            credits = 0
        
        welcome_text = (
            f"🔱 *BIENVENIDO A HADES V1* 🔱\n\n"
            f"👤 Usuario: `{update.effective_user.username or 'Usuario'}`\n"
            f"🆔 ID: `{user_id}`\n"
            f"💰 Créditos: `{credits}`\n\n"
            f"Selecciona una opción del menú:"
        )
        
        await query.edit_message_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard(user_id)
        )
        return
    
    # ========== AYUDA - No requiere key ==========
    elif data == "menu:ayuda":
        await query.edit_message_text(
            "❓ *AYUDA*\n\n"
            "*Comandos principales:*\n"
            "• `/start` - Menú principal\n"
            "• `/activar <clave>` - Activar tu clave\n"
            "• `/status` - Ver estado de tu clave\n\n"
            "📲 Ingresa al grupo https://t.me/EternalTartaro\n"
            "y pregunta por el staff con /staff para obtener acceso.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")
            ]])
        )
        return
    
    elif data == "menu:cookie":
        # Redirigir al menú de cookies
        await cookie_start(update, context)
        return
    
    # ========== PERFIL - Siempre visible ==========
    elif data == "menu:perfil":
        status = key_manager.get_user_status(user_id)
        
        if not status:
            await query.edit_message_text(
                "👤 *TU PERFIL*\n\n"
                f"🆔 ID: `{user_id}`\n"
                "🔒 *Sin acceso activo*\n\n"
                "📲 Ingresa al grupo https://t.me/EternalTartaro\n"
                "y pregunta por el staff con /staff para obtener acceso.\n\n"
                "Una vez tengas tu key, usa `/activar <clave>` para activarla.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")
                ]])
            )
            return
        
        # Si tiene key, mostrar info completa...
        plan = status.get("plan", "1SEMA")
        plan_info = PLANES.get(plan, PLANES['1SEMA'])
        
        if status["is_expired"]:
            estado = "❌ Expirada"
        else:
            estado = f"✅ Activa ({status['days_remaining']} días)"
        
        perfil_text = (
            "👤 *TU PERFIL*\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"📋 Plan: *{plan}*\n"
            f"📝 {plan_info['descripcion']}\n"
            f"🔹 Estado: {estado}\n"
            f"📅 Expira: {datetime.fromisoformat(status['expires_at']).strftime('%Y-%m-%d')}\n\n"
            f"🔑 Tu clave: `{status['key']}`"
        )
        
        await query.edit_message_text(
            perfil_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")
            ]])
        )
        return
    
    # ========== MENÚ DE GATES - TODOS PUEDEN VER ==========
    elif data.startswith("menu:gates:page:"):
        page = int(data.split(":")[-1])
        
        await query.edit_message_text(
            "🚪 *GATES DISPONIBLES*\n\n"
            "Selecciona un gate para ver opciones:\n\n"
            "🔓 = Acceso disponible\n"
            "🔒 = Requiere plan superior\n\n"
            "🟢 Online | 🔴 Offline",
            parse_mode='Markdown',
            reply_markup=get_gates_paginated_keyboard(user_id, page)
        )
        return  # Importante: return aquí para no caer en la verificación de has_key
    
    # ========== GATE ESPECÍFICO - TODOS PUEDEN VER, PERO BLOQUEADOS ==========
    elif data.startswith("gate:"):
        gate_key = data.split(":")[1]
        gate_data = GATES_CONFIG.get(gate_key, {})
        
        if not gate_data:
            await query.edit_message_text("❌ Gate no encontrado.")
            return
        
        # Obtener plan del usuario (puede ser None si no tiene)
        user_plan = key_manager.get_user_plan(user_id)
        if not user_plan:
            user_plan = 'SIN_PLAN'
        
        # Verificar acceso
        required_plans = gate_data.get('plan_required', [])
        has_access = user_plan in required_plans or key_manager.is_admin(user_id)
        
        # Si NO tiene acceso, mostrar mensaje de bloqueo
        if not has_access:
            planes_con_acceso = ', '.join(required_plans)
            
            bloqueo_text = (
                f"🔒 *{gate_data['name']} BLOQUEADO*\n\n"
                f"📝 {gate_data['description']}\n\n"
                f"❌ *Tu estado:* Sin plan activo\n"
                f"✅ *Planes con acceso:* {planes_con_acceso}\n\n"
                f"🔑 Para desbloquear este gate, necesitas:\n"
            )
            
            if 'OLIMPO' in required_plans and '1MES' in required_plans:
                bloqueo_text += "• Plan OLIMPO o 1MES\n"
            elif 'OLIMPO' in required_plans and '1MES' in required_plans and '15DIAS' in required_plans:
                bloqueo_text += "• Plan OLIMPO, 1MES o 15DIAS\n"
            else:
                bloqueo_text += f"• Plan {planes_con_acceso}\n"
            
            bloqueo_text += (
                "\n\n📲 Ingresa al grupo [Eternal Tartaro](https://t.me/EternalTartaro)\n"
                "y pregunta por el staff con `/staff` para obtener acceso."
            )
            
            await query.edit_message_text(
                bloqueo_text,
                parse_mode='Markdown',
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📲 Ir al Grupo", url="https://t.me/EternalTartaro")],
                    [InlineKeyboardButton("⬅️ Volver a Gates", callback_data="menu:gates:page:1")],
                    [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
                ])
            )
            return
        
        # Si tiene acceso, mostrar menú normal
        gate_text = (
            f"{gate_data['emoji']} *{gate_data['name']}*\n\n"
            f"📝 {gate_data['description']}\n"
            f"📌 Comando: `/{gate_data['command']}`\n"
            f"🟢 Estado: {gate_data['status']}\n\n"
            f"Selecciona una acción:"
        )
        
        await query.edit_message_text(
            gate_text,
            parse_mode='Markdown',
            reply_markup=get_gate_action_keyboard(gate_key)
        )
        return  # Importante: return aquí también
    
    
    # ========== ACCIONES DE GATE (Ver Comando, Verificar, etc.) ==========
    elif data.startswith("gateaction:"):
        parts = data.split(":")
        gate_key = parts[1]
        action = parts[2]
        gate_data = GATES_CONFIG.get(gate_key, {})
        
        if not gate_data:
            await query.edit_message_text("❌ Gate no encontrado.")
            return
        
        # Verificar si tiene acceso
        user_plan = key_manager.get_user_plan(user_id)
        if not user_plan:
            user_plan = 'SIN_PLAN'
        
        required_plans = gate_data.get('plan_required', [])
        has_access = user_plan in required_plans or key_manager.is_admin(user_id)
        
        if not has_access:
            await query.edit_message_text(
                "🔒 *Acceso Denegado*\n\n"
                "No tienes permiso para usar este gate.\n\n"
                "📲 Ingresa al grupo [Eternal Tartaro](https://t.me/EternalTartaro)\n"
                "y pregunta por el staff con `/staff` para obtener acceso.",
                parse_mode='Markdown',
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📲 Ir al Grupo", url="https://t.me/EternalTartaro")],
                    [InlineKeyboardButton("⬅️ Volver a Gates", callback_data="menu:gates:page:1")]
                ])
            )
            return
        
        command = gate_data.get('command', gate_key)
        
        if action == "info":
            # Mostrar información del comando - Cronos es single only
            if gate_key == "cronos":
                info_text = (
                    f"{gate_data['emoji']} *{gate_data['name']}*\n\n"
                    f"📝 {gate_data['description']}\n\n"
                    f"📌 *Comando:* `/{command}`\n"
                    f"⏳ *Modo:* Single only (antispam 30s)\n\n"
                    f"*Formato:*\n"
                    f"`/{command} <cc>|<mes>|<año>|<cvv>`\n\n"
                    f"*Ejemplo:*\n"
                    f"`/{command} 4532640527811643|12|2025|123`"
                )
            else:
                info_text = (
                    f"{gate_data['emoji']} *{gate_data['name']}*\n\n"
                    f"📝 {gate_data['description']}\n\n"
                    f"📌 *Comando:* `/{command}`\n"
                    f"📌 *Comando Mass:* `/{command}mass`\n\n"
                    f"*Formato:*\n"
                    f"`/{command} <cc>|<mes>|<año>|<cvv>`\n\n"
                    f"*Ejemplo:*\n"
                    f"`/{command} 4532640527811643|12|2025|123`"
                )
            
            await query.edit_message_text(
                info_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Volver", callback_data=f"gate:{gate_key}")],
                    [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
                ])
            )
        
        elif action == "single":
            await query.edit_message_text(
                f"▶️ *Verificar 1 CC*\n\n"
                f"Usa el comando directamente:\n"
                f"`/{command} <cc>|<mes>|<año>|<cvv>`\n\n"
                f"*Ejemplo:*\n"
                f"`/{command} 4532640527811643|12|2025|123`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Volver", callback_data=f"gate:{gate_key}")],
                    [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
                ])
            )
        
        elif action == "mass":
            if gate_key == "cronos":
                await query.edit_message_text(
                    f"⏳ *Cronos es Single Only*\n\n"
                    f"Este gate solo permite 1 tarjeta por vez con antispam de 30s.\n\n"
                    f"Usa directamente:\n"
                    f"`/{command} <cc>|<mes>|<año>|<cvv>`\n\n"
                    f"*Ejemplo:*\n"
                    f"`/{command} 4532640527811643|12|2025|123`",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Volver", callback_data=f"gate:{gate_key}")],
                        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
                    ])
                )
            else:
                await query.edit_message_text(
                    f"▶️ *Verificar Mass*\n\n"
                    f"Responde a un mensaje con tarjetas usando:\n"
                    f"`/{command}mass`\n\n"
                    f"O escribe las tarjetas después del comando:\n"
                    f"`/{command}mass`\n"
                    f"(y las tarjetas en el siguiente mensaje)",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Volver", callback_data=f"gate:{gate_key}")],
                        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
                    ])
                )
        
        
        return
    
    
    # ========== A PARTIR DE AQUÍ SÍ REQUIERE KEY ==========
    
    # Verificar si tiene key para el resto de funciones
    has_key = key_manager.has_active_key(user_id) or key_manager.is_admin(user_id)
    
    if not has_key:
        await query.edit_message_text(
            "🔒 *Acceso Restringido*\n\n"
            "No tienes una clave activa.\n\n"
            "📲 Ingresa al grupo [Eternal Tartaro](https://t.me/EternalTartaro)\n"
            "y pregunta por el staff con `/staff` para obtener acceso.\n\n"
            "Una vez tengas tu key, usa `/activar <clave>` para activarla.",
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📲 Ir al Grupo", url="https://t.me/EternalTartaro")],
                [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
            ])
        )
        return
    
    # ========== FUNCIONES QUE REQUIEREN KEY ==========
    
    # MENÚ GENERAR
    if data == "menu:generar":
        await query.edit_message_text(
            "⚡ *MENÚ GENERAR*\n\n"
            "Selecciona una opción:",
            parse_mode='Markdown',
            reply_markup=get_generar_menu_keyboard()
        )
    
    # ... (resto del código para generar, admin, etc. sin cambios)
    
    elif data.startswith("generar:"):
        action = data.split(":")[1]
        
        if action == "cc":
            await query.edit_message_text(
                "🎴 *Generar Tarjetas*\n\n"
                "Usa el comando:\n"
                "`/gen <BIN>|<mes>|<año>|<cvv>`\n\n"
                "Ejemplo:\n"
                "`/gen 457249651136xxxx|06|2029|xxx`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="menu:generar")
                ]])
            )
        elif action == "bin":
            await query.edit_message_text(
                "🔢 *Buscar Información de BIN*\n\n"
                "Usa el comando:\n"
                "`/bin <BIN>`\n\n"
                "Ejemplo:\n"
                "`/bin 457249`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="menu:generar")
                ]])
            )
        elif action == "fake":
            await query.edit_message_text(
                "🎭 *Generar Datos Falsos*\n\n"
                "Usa el comando:\n"
                "`/fake <país>`\n\n"
                "Países disponibles:\n"
                "• `mx` - México\n"
                "• `us` - Estados Unidos\n"
                "• `jp` - Japón\n\n"
                "Ejemplo:\n"
                "`/fake mx`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="menu:generar")
                ]])
            )
        elif action == "extra":
            await query.edit_message_text(
                "🧬 *Extrapolar BINs*\n\n"
                "Usa el comando:\n"
                "`/extra`\n\n"
                "Te guiaré paso a paso para extrapolar BINs desde lives o generar variantes.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="menu:generar")
                ]])
            )
    
    # STATS
    elif data == "menu:stats":
        await query.edit_message_text(
            "📊 *ESTADÍSTICAS*\n\n"
            "Esta función estará disponible próximamente.\n\n"
            "Mientras tanto, usa /status para ver tu información.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")
            ]])
        )
    
    # CONFIG
    elif data == "menu:config":
        await query.edit_message_text(
            "⚙️ *CONFIGURACIÓN*\n\n"
            "Opciones disponibles:\n\n"
            "🍪 *Cookies de Amazon*\n"
            "Usa `/addcookie <cookies>` para guardar tus cookies.\n\n"
            "🔔 *Notificaciones*\n"
            "Próximamente...",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")
            ]])
        )
    
    # PANEL ADMIN
    elif data == "menu:admin":
        if not key_manager.is_admin(user_id):
            await query.edit_message_text("❌ No tienes permisos.")
            return
        
        await query.edit_message_text(
            "🔧 *PANEL DE ADMINISTRADOR*\n\n"
            "*Comandos disponibles:*\n"
            "• `/genkey <user_id> <plan> <dias>` - Generar clave\n"
            "• `/addadmin <user_id>` - Agregar admin\n"
            "• `/listadmins` - Ver admins\n"
            "• `/listusers` - Ver usuarios\n"
            "• `/revoke <user_id>` - Revocar clave\n"
            "• `/extend <user_id> <dias>` - Extender clave\n"
            "• `/export` - Exportar datos\n"
            "• `/import` - Importar datos\n\n"
            "Selecciona una opción:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]
            ])
        )

# ============ NUEVA FUNCIÓN START ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el menú principal con botones"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Usuario"
    
    # Obtener créditos de cookies
    try:
        cookie_user = await get_cookie_user(user_id)
        credits = cookie_user.get('credits', 0)
    except:
        credits = 0
    
    welcome_text = (
        f"🔱 *BIENVENIDO A HADES V1* 🔱\n\n"
        f"👤 Usuario: `{username}`\n"
        f"🆔 ID: `{user_id}`\n"
        f"💰 Créditos: `{credits}`\n\n"
        f"Selecciona una opción del menú:"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(user_id)
    )


def init_default_admin():
    """Inicializa admins y carga superadmins desde BD"""
    global SUPERADMINS
    
    # Inicializar tabla de superadmins
    init_superadmins_table()
    
    # Cargar superadmins desde BD
    SUPERADMINS = load_superadmins_from_db()
    print(f"👑 Superadmins cargados: {SUPERADMINS}")
    
    # Asegurar que el creador esté en admins normales también
    DEFAULT_ADMIN_ID = 5531198491
    
    try:
        admins = key_manager.get_admins()
        
        if not admins:
            key_manager.add_admin(DEFAULT_ADMIN_ID, added_by=DEFAULT_ADMIN_ID)
            print(f"✅ Admin por defecto agregado: {DEFAULT_ADMIN_ID}")
        else:
            print(f"ℹ️ {len(admins)} administrador(es) existente(s)")
            
    except Exception as e:
        print(f"⚠️ Error al inicializar admin: {e}")

def main():
    init_default_admin()
    
        # === INICIO INICIALIZACIÓN COOKIES ===
    # Inicializar base de datos de cookies (async)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_cookie_db())
    print("✅ Sistema de cookies inicializado")
    
    # Inicializar tabla de auditoría
    asyncio.get_event_loop().run_until_complete(init_audit_table())
    print("✅ Sistema de auditoría inicializado")
    # === FIN INICIALIZACIÓN COOKIES ===
    
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("extra", extra_start)],
        states={
            ESTADO_MENU: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, menu_opcion)
            ],
            ESTADO_EXTRAPOLAR: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_bin)
            ],
            ESTADO_LIVES: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_lives)
            ],
            ESTADO_BIN: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_csv)
            ],
            ESTADO_MODO: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_modo)
            ],
            ESTADO_BIN_LEN: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_bin_len)
            ],
            ESTADO_CANTIDAD: [
                CommandHandler("cancel", cancelar),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cantidad)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancelar)],
    )
    
    # ========== HANDLERS DE NAVEGACIÓN DE LISTAS ==========
    # Deben ir ANTES que los handlers generales de menú
    application.add_handler(CallbackQueryHandler(
        list_navigation_handler, 
        pattern=r"^(adminlist:|userlist:)"
    ))
    
    # ========== HANDLERS DE BOTONES DEL MENÚ ==========
    application.add_handler(CallbackQueryHandler(
        button_callback_handler, 
        pattern=r"^(menu:|gate:|gateaction:|generar:|admin:)"
    ))
    
    # ========== HANDLERS DE BOTONES DEL MENÚ (DEBEN IR PRIMERO) ==========
    application.add_handler(CallbackQueryHandler(button_callback_handler, pattern=r"^(menu:|gate:|gateaction:|generar:|admin:)"))
    
    # ========== HANDLER PARA REGENERAR (SOLO callbacks específicos) ==========
    # Usar r"" para raw string y evitar el warning
    application.add_handler(CallbackQueryHandler(regenerate_callback, pattern=r"^(regenerate|generate0)\|"))
    
    # ========== COMANDOS ==========
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("gen", generate))
    application.add_handler(CommandHandler("gen0", generate0))
    application.add_handler(CommandHandler("bin", bin_command))
    application.add_handler(CommandHandler("addcookie", add_cookie_command))
    application.add_handler(CommandHandler("az", amazon_command))
    application.add_handler(CommandHandler("azmass", amazon_mass_command))
    application.add_handler(CommandHandler("fake", fake_command))
    
    application.add_handler(conv_handler)
    
    # Comandos de gestión de claves
    application.add_handler(CommandHandler("activar", activate_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("myid", myid_command))

    # ========== COMANDOS DE ADMINISTRACIÓN CON ROLES ==========
    application.add_handler(CommandHandler("addadmin", add_admin_command))      # Solo Superadmins
    application.add_handler(CommandHandler("addsuperadmin", add_superadmin_command))  # Solo creador
    application.add_handler(CommandHandler("removeadmin", remove_admin_command))      # Solo Superadmins
    application.add_handler(CommandHandler("listadmins", list_admins_command))        # Cualquier admin
    
    # Administradores
    application.add_handler(CommandHandler("genkey", genkey_command))
    application.add_handler(CommandHandler("listusers", list_users_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(CommandHandler("extend", extend_command))
    application.add_handler(CommandHandler("export", export_data_command))
    application.add_handler(CommandHandler("exportusers", export_users_command))
    application.add_handler(CommandHandler("exportadmins", export_admins_command))
    application.add_handler(CommandHandler("import", import_data_command))
    application.add_handler(CommandHandler("restoreall", restore_all_command))
    
    # Comandos de gates
    application.add_handler(CommandHandler("di", euridice_command))
    application.add_handler(CommandHandler("dimass", euridice_mass_command))
    application.add_handler(CommandHandler("ph", philotes_command))
    application.add_handler(CommandHandler("phmass", philotes_mass_command))
    application.add_handler(CommandHandler("eu", eurinias_command))
    application.add_handler(CommandHandler("eumass", eurinias_mass_command))
    application.add_handler(CommandHandler("wo", wojtek_command))
    application.add_handler(CommandHandler("womass", wojtek_mass_command))
    # Cronos PayPal $0.10 USD - SINGLE ONLY (no mass) con antispam 30s
    application.add_handler(CommandHandler("cr", cronos_command))
    
    # === INICIO REGISTRO HANDLERS COOKIES ===
    # Comandos de cookies
    application.add_handler(CommandHandler("cookie", cookie_start))
    application.add_handler(CommandHandler("cookiestatus", cookie_status_cmd))
    application.add_handler(CommandHandler("addcookiecredits", cookie_addcredits_cmd))
    
    # COMANDO SECRETO - CORTE (solo superadmin)
    application.add_handler(CommandHandler("corte", corte_command))
    
    # COMANDO SECRETO - CORTETOTAL (exporta todo a CSV)
    application.add_handler(CommandHandler("cortetotal", corte_total_command))
    
    # Callbacks de cookies
    application.add_handler(CallbackQueryHandler(cookie_button_handler, pattern=r"^cookie:"))
    # === FIN REGISTRO HANDLERS COOKIES ===
    application.add_handler(CommandHandler("usocreditos", usocreditos_command))
    
    # Comandos de admin para créditos
    application.add_handler(CommandHandler("exportcredits", export_credits_command))
    
    # Comandos de exportación de créditos
    application.add_handler(CommandHandler("usercredits", usercredits_command))
    application.add_handler(CommandHandler("topcredits", topcredits_command))
    
    application.add_handler(CommandHandler("listcredits", listcredits_command))
    application.add_handler(CommandHandler("exportallcredits", exportallcredits_command))
    
    application.add_handler(CommandHandler("at", atlantic_command))
    application.add_handler(CommandHandler("atmass", atlantic_mass_command))
    
        
    
    
    try:
        print("🤖 Iniciando Hades V1...")
        application.run_polling()
    except KeyboardInterrupt:
        print("👋 Bot detenido por el usuario.")
    except Exception as e:
        print(f"💥 Error: {e}")

if __name__ == "__main__":
    main()
