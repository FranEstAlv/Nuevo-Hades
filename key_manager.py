import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3
import hashlib

class KeyManager:
    def __init__(self, db_file="data/db/keys.db"):
        """Inicializa el gestor de claves con SQLite o PostgreSQL"""
        self.db_file = db_file
        self.is_postgres = isinstance(db_file, str) and (
            db_file.startswith("postgresql://") or
            db_file.startswith("postgres://")
        )

        if self.is_postgres:
            try:
                import psycopg2
                self.postgres_url = db_file
                self._init_postgres()
            except ImportError:
                raise ImportError("Instala psycopg2: pip install psycopg2-binary")
        else:
            self._init_db()

    def _get_conn(self):
        """Obtiene una conexión a la base de datos"""
        if self.is_postgres:
            import psycopg2
            return psycopg2.connect(self.postgres_url)
        else:
            return sqlite3.connect(self.db_file)

    @contextmanager
    def _conn(self):
        """Conexión que SIEMPRE se cierra al salir, haya éxito, return
        temprano o excepción -- a diferencia de `with self._get_conn():`,
        que en sqlite3/psycopg2 solo controla el commit/rollback y nunca
        cierra la conexión."""
        conn = self._get_conn()
        try:
            yield conn
        finally:
            conn.close()

    def _get_placeholder(self):
        """Retorna el placeholder correcto para cada BD"""
        return "%s" if self.is_postgres else "?"

    def _init_db(self):
        """Crea las tablas necesarias si no existen (SQLite)"""
        with self._conn() as conn:
            cursor = conn.cursor()
            self._create_tables_sqlite(cursor)
            conn.commit()

    def _init_postgres(self):
        """Crea las tablas necesarias si no existen (PostgreSQL)"""
        with self._conn() as conn:
            cursor = conn.cursor()
            self._create_tables_postgres(cursor)
            conn.commit()

    def _create_tables_sqlite(self, cursor):
        """Crea tablas para SQLite"""
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS keys (
            key TEXT PRIMARY KEY,
            plan TEXT DEFAULT '1SEMA',
            created_at TEXT,
            expires_at TEXT,
            days INTEGER,
            user_id INTEGER,
            used BOOLEAN DEFAULT 0,
            used_by INTEGER DEFAULT NULL,
            used_at TEXT DEFAULT NULL
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_keys (
            user_id INTEGER PRIMARY KEY,
            key TEXT,
            plan TEXT DEFAULT '1SEMA',
            activated_at TEXT,
            expires_at TEXT,
            FOREIGN KEY (key) REFERENCES keys (key)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_at TEXT,
            added_by INTEGER
        )
        ''')

    def _create_tables_postgres(self, cursor):
        """Crea tablas para PostgreSQL"""
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS keys (
            key VARCHAR(255) PRIMARY KEY,
            plan VARCHAR(50) DEFAULT '1SEMA',
            created_at TIMESTAMP,
            expires_at TIMESTAMP,
            days INTEGER,
            user_id BIGINT,
            used BOOLEAN DEFAULT FALSE,
            used_by BIGINT DEFAULT NULL,
            used_at TIMESTAMP DEFAULT NULL
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_keys (
            user_id BIGINT PRIMARY KEY,
            key VARCHAR(255),
            plan VARCHAR(50) DEFAULT '1SEMA',
            activated_at TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (key) REFERENCES keys (key)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            added_at TIMESTAMP,
            added_by BIGINT
        )
        ''')

    def add_admin(self, user_id: int, added_by: int = None):
        """Agrega un administrador al sistema"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            if self.is_postgres:
                cursor.execute(f'''
                INSERT INTO admins (user_id, added_at, added_by)
                VALUES ({ph}, {ph}, {ph})
                ON CONFLICT (user_id) DO UPDATE SET added_at = EXCLUDED.added_at
                ''', (user_id, datetime.now(), added_by))
            else:
                cursor.execute('INSERT OR REPLACE INTO admins VALUES (?, ?, ?)',
                              (user_id, datetime.now().isoformat(), added_by))

            conn.commit()
            return True

    def remove_admin(self, user_id: int):
        """Elimina un administrador del sistema"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            cursor.execute(f"DELETE FROM admins WHERE user_id = {ph}", (user_id,))

            conn.commit()
            return cursor.rowcount > 0

    def is_admin(self, user_id: int) -> bool:
        """Verifica si un usuario es administrador"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            cursor.execute(f"SELECT user_id FROM admins WHERE user_id = {ph}", (user_id,))
            result = cursor.fetchone()

            return result is not None

    def get_admins(self) -> List[int]:
        """Obtiene la lista de todos los administradores"""
        with self._conn() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT user_id FROM admins")
            return [row[0] for row in cursor.fetchall()]

    def generate_key(self, user_id: int, plan: str, days: int, generated_by: int) -> str:
        """Genera una nueva clave para un usuario con plan específico"""
        import random
        created_at = datetime.now()
        expires_at = created_at + timedelta(days=days)

        max_retries = 10
        for attempt in range(max_retries):
            suffix = f"_{random.randint(1000, 9999)}_{attempt}" if attempt > 0 else ""
            key_data = f"{user_id}{plan}{days}{datetime.now().isoformat()}{suffix}"
            key = hashlib.md5(key_data.encode()).hexdigest()[:16].upper()

            with self._conn() as conn:
                cursor = conn.cursor()
                ph = self._get_placeholder()

                try:
                    if self.is_postgres:
                        cursor.execute(f'''
                        INSERT INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                        ''', (key, plan, created_at, expires_at, days, user_id, False))
                    else:
                        cursor.execute('''
                        INSERT INTO keys (key, plan, created_at, expires_at, days, user_id, used)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (key, plan, created_at.isoformat(), expires_at.isoformat(), days, user_id, 0))

                    conn.commit()
                    return key

                except sqlite3.IntegrityError:
                    continue
                except Exception as e:
                    error_msg = str(e).lower()
                    if 'unique' in error_msg or 'duplicate' in error_msg:
                        continue
                    raise

        raise RuntimeError("No se pudo generar una clave única tras varios intentos")

    def activate_key(self, key: str, user_id: int) -> Tuple[bool, str]:
        """Activa una clave para un usuario"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            cursor.execute(f'''
            SELECT key, plan, expires_at, used FROM keys WHERE key = {ph}
            ''', (key,))

            key_data = cursor.fetchone()

            if not key_data:
                return False, "La clave no existe"

            db_key, plan, expires_at, used = key_data

            if self.is_postgres:
                is_used = used if isinstance(used, bool) else bool(used)
            else:
                is_used = used == 1

            if is_used:
                return False, "La clave ya ha sido utilizada"

            if isinstance(expires_at, str):
                expires_date = datetime.fromisoformat(expires_at)
            else:
                expires_date = expires_at

            if datetime.now() > expires_date:
                return False, "La clave ha expirado"

            used_val = True if self.is_postgres else 1
            cursor.execute(f'''
            UPDATE keys SET used = {ph}, used_by = {ph}, used_at = {ph} WHERE key = {ph}
            ''', (used_val, user_id, datetime.now(), key))

            if self.is_postgres:
                cursor.execute(f'''
                INSERT INTO active_keys (user_id, key, plan, activated_at, expires_at)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                ON CONFLICT (user_id) DO UPDATE SET
                    key = EXCLUDED.key,
                    plan = EXCLUDED.plan,
                    activated_at = EXCLUDED.activated_at,
                    expires_at = EXCLUDED.expires_at
                ''', (user_id, key, plan, datetime.now(), expires_date))
            else:
                cursor.execute('''
                INSERT OR REPLACE INTO active_keys (user_id, key, plan, activated_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ''', (user_id, key, plan, datetime.now().isoformat(),
                      expires_at if isinstance(expires_at, str) else expires_date.isoformat()))

            conn.commit()

            return True, "Clave activada correctamente"

    def get_user_status(self, user_id: int) -> Optional[Dict]:
        """Obtiene el estado de la clave de un usuario incluyendo el plan"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            cursor.execute(f'''
            SELECT key, plan, activated_at, expires_at FROM active_keys WHERE user_id = {ph}
            ''', (user_id,))

            user_data = cursor.fetchone()

            if not user_data:
                return None

            key, plan, activated_at, expires_at = user_data

            if isinstance(expires_at, str):
                expires_date = datetime.fromisoformat(expires_at)
            else:
                expires_date = expires_at

            is_expired = datetime.now() > expires_date

            return {
                "user_id": user_id,
                "key": key,
                "plan": plan if plan else "1SEMA",
                "activated_at": activated_at.isoformat() if isinstance(activated_at, datetime) else activated_at,
                "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
                "is_expired": is_expired,
                "days_remaining": max(0, (expires_date - datetime.now()).days)
            }

    def get_user_plan(self, user_id: int) -> Optional[str]:
        """Obtiene solo el plan del usuario"""
        status = self.get_user_status(user_id)
        if status:
            return status.get("plan", "1SEMA")
        return None

    def has_active_key(self, user_id: int) -> bool:
        """Verifica si un usuario tiene una clave activa"""
        status = self.get_user_status(user_id)
        if not status:
            return False
        return not status["is_expired"]

    def revoke_key(self, user_id: int) -> bool:
        """Revoca la clave activa de un usuario"""
        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            cursor.execute(f"DELETE FROM active_keys WHERE user_id = {ph}", (user_id,))

            conn.commit()
            return cursor.rowcount > 0

    def extend_key(self, user_id: int, days: int) -> Tuple[bool, str]:
        """Extiende la validez de la clave de un usuario"""
        status = self.get_user_status(user_id)

        if not status:
            return False, "El usuario no tiene una clave activa"

        current_expires = datetime.fromisoformat(status["expires_at"]) if isinstance(status["expires_at"], str) else status["expires_at"]
        new_expires = max(datetime.now(), current_expires) + timedelta(days=days)

        with self._conn() as conn:
            cursor = conn.cursor()
            ph = self._get_placeholder()

            new_expires_val = new_expires if self.is_postgres else new_expires.isoformat()

            cursor.execute(f'''
            UPDATE active_keys SET expires_at = {ph} WHERE user_id = {ph}
            ''', (new_expires_val, user_id))

            conn.commit()

            return True, f"Clave extendida {days} días. Nueva fecha: {new_expires.strftime('%Y-%m-%d %H:%M:%S')}"

    def get_all_active_users(self) -> List[Dict]:
        """Obtiene todos los usuarios con claves activas"""
        with self._conn() as conn:
            cursor = conn.cursor()

            cursor.execute('''
            SELECT user_id, key, plan, activated_at, expires_at FROM active_keys
            ''')

            users = []
            for row in cursor.fetchall():
                user_id, key, plan, activated_at, expires_at = row

                if isinstance(expires_at, str):
                    expires_date = datetime.fromisoformat(expires_at)
                else:
                    expires_date = expires_at

                is_expired = datetime.now() > expires_date

                users.append({
                    "user_id": user_id,
                    "key": key,
                    "plan": plan if plan else "1SEMA",
                    "activated_at": activated_at.isoformat() if isinstance(activated_at, datetime) else activated_at,
                    "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
                    "is_expired": is_expired,
                    "days_remaining": max(0, (expires_date - datetime.now()).days)
                })

            return users
