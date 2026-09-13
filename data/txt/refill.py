import os
import random
import time
import requests
import string
import csv

API_BASE = "https://parkingpay-api-prod.azurewebsites.net"
REGISTER_URL = f"{API_BASE}/api/app/usuarios/registro"
LOGIN_URL = f"{API_BASE}/api/auth"

HEADERS = {
    "user-agent": "Dart/2.18 (dart:io)",
    "content-type": "application/json; charset=utf-8"
}

NOMBRES = ["Juan","Pedro","Luis","Carlos","Miguel","Jose","Francisco","Antonio","Alejandro","Javier",
           "Ricardo","Fernando","Roberto","Sergio","Arturo","Maria","Ana","Laura","Carmen","Rosa",
           "Guadalupe","Martha","Patricia","Gabriela","Alejandra"]
APELLIDOS = ["Garcia","Lopez","Martinez","Rodriguez","Hernandez","Gonzalez","Perez","Sanchez",
             "Ramirez","Cruz","Flores","Morales","Vazquez","Jimenez","Torres","Reyes","Castillo",
             "Ortiz","Mendoza","Ruiz","Molina","Romero","Ramos","Diaz"]
DOMINIOS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me"]

def random_string(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def random_phone():
    lada = random.choice(["449","222","477","686","664","612","667"])
    return lada + "".join(random.choices("0123456789", k=7))

def random_email():
    return f"{random_string(8)}@{random.choice(DOMINIOS)}"

def random_password():
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))

def format_proxy(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # Si ya tiene @, es válida
        if "@" in s:
            return s
        # Quitar el esquema para analizar el resto
        scheme = s.split("://")[0] + "://"
        rest = s.split("://", 1)[1]
        parts = rest.split(":")
        # Formato: usuario:contraseña:host:puerto
        if len(parts) == 4:
            user, pwd, host, port = parts
            return f"{scheme}{user}:{pwd}@{host}:{port}"
        # Formato: host:puerto (sin autenticación)
        elif len(parts) == 2:
            return s
        else:
            return None
    # Sin esquema
    parts = s.split(":")
    if len(parts) == 4:
        if "." in parts[2]:
            user, pwd, host, port = parts
        else:
            host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{s}"
    return None

def cargar_proxies():
    if not os.path.exists("proxies.txt"):
        return []
    with open("proxies.txt") as f:
        raw = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    proxies = []
    for p in raw:
        url = format_proxy(p)
        if url:
            proxies.append(url)
    return proxies

def crear_cuenta(proxy_url):
    nombre = random.choice(NOMBRES)
    apellido = random.choice(APELLIDOS)
    email = random_email()
    telefono = random_phone()
    password = random_password()

    datos = {
        "Nombre": nombre,
        "Apellidos": apellido,
        "Telefono": telefono,
        "CorreoElectronico": email,
        "Contrasena": password,
        "ConfirmarContrasena": password,
    }

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        r = requests.post(REGISTER_URL, json=datos, headers=HEADERS, timeout=15, proxies=proxies)
        if r.status_code == 403 and "stopped" in r.text:
            return None, "API_APAGADA"
        if r.status_code not in (200, 201):
            return None, f"REG_ERROR_{r.status_code}"

        if r.text.strip() != "true":
            return None, "REG_RESPONSE_FALSE"

        login_data = {
            "CorreoElectronico": email,
            "Contrasena": password
        }
        r2 = requests.post(LOGIN_URL, json=login_data, headers=HEADERS, timeout=15, proxies=proxies)
        if r2.status_code != 200:
            return None, f"LOGIN_ERROR_{r2.status_code}"

        data = r2.json()
        token = data.get("token") or data.get("accessToken")
        if not token:
            return None, "NO_TOKEN"

        return token, {"email": email, "password": password, "token": token}

    except Exception as e:
        return None, str(e)[:30]

def guardar_token(token):
    with open("token1.txt", "a") as f:
        f.write(token + "\n")

def guardar_cuenta(cuenta):
    with open("cuentas.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if os.path.getsize("cuentas.csv") == 0:
            writer.writerow(["email", "password", "token"])
        writer.writerow([cuenta["email"], cuenta["password"], cuenta["token"]])

def main():
    cantidad = 100
    proxies_list = cargar_proxies()
    if not proxies_list:
        print("No hay proxies en proxies.txt, se usará conexión directa.")
        proxies_list = [None]

    creados = 0
    intentos = 0
    max_intentos = cantidad * 3

    print(f"Generando {cantidad} tokens...")
    while creados < cantidad and intentos < max_intentos:
        intentos += 1
        proxy_url = random.choice(proxies_list) if proxies_list else None
        token, info = crear_cuenta(proxy_url)

        if token:
            guardar_token(token)
            if isinstance(info, dict):
                guardar_cuenta(info)
            creados += 1
            print(f"[{creados}/{cantidad}] OK")
        else:
            if info == "API_APAGADA":
                print("API apagada. Deteniendo.")
                break
            print(f"[{creados}/{cantidad}] Error: {info}")

        time.sleep(random.uniform(1.0, 2.0))

    print(f"\nFinalizado. {creados} tokens generados.")

if __name__ == "__main__":
    main()
