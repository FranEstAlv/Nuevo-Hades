import random
import re
import csv
from datetime import datetime

class CreditCardGenerator:
    @staticmethod
    def generate_cc(number, month, year, cvv, quantity=10):
        """
        Genera números de tarjetas de crédito válidos usando el algoritmo de Luhn.
        """
        # Validar el formato del número
        if not re.match(r'^[0-9xX]+$', number):
            raise ValueError("El número solo puede contener dígitos y 'x'")
        
        # Extraer el BIN (primeros 6 dígitos)
        bin_number = number[:6].replace('x', '0').replace('X', '0')
        
        # Obtener información del BIN desde el CSV
        bin_info = CreditCardGenerator._get_bin_info(bin_number)
        
        # Determinar la longitud de la tarjeta según el BIN
        card_length = CreditCardGenerator._get_card_length(bin_info)
        
        # Determinar la longitud del CVV según el BIN
        cvv_length = CreditCardGenerator._get_cvv_length(bin_info)
        
        cards = []
        for _ in range(quantity):
            # Generar el número de tarjeta válido
            cc_number = CreditCardGenerator._generate_card_number(number, card_length)
            
            # Generar mes de vencimiento
            exp_month = CreditCardGenerator._generate_month(month)
            
            # Generar año de vencimiento
            exp_year = CreditCardGenerator._generate_year(year)
            
            # Generar CVV
            cvv_gen = CreditCardGenerator._generate_cvv(cvv, cvv_length)
            
            # Agregar la tarjeta
            cards.append(f"{cc_number}|{exp_month}|{exp_year}|{cvv_gen}")
        
        return cards
    
    @staticmethod
    def _get_bin_info(bin_number):
        """
        Obtiene información del BIN desde el archivo tarjetas.csv
        """
        try:
            with open('tarjetas.csv', mode='r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    if row['bin'].strip() == bin_number:
                        return row
        except FileNotFoundError:
            print("El archivo tarjetas.csv no fue encontrado.")
        except Exception as e:
            print(f"Error al leer el archivo CSV: {e}")
        
        # Si no se encuentra el BIN, devolver información por defecto
        return {
            'brand': 'VISA',
            'tipo': 'DEBIT',
            'nivel': 'CLASSIC',
            'Banco': 'BANCO UNKNOWN',
            'teléfono': 'UNKNOWN',
            'país': 'UNKNOWN'
        }
    
    @staticmethod
    def _get_card_length(bin_info):
        """
        Determina la longitud de la tarjeta según el BIN
        """
        brand = bin_info.get('brand', '').lower()
        
        if brand == 'american express':
            return 15
        elif brand == 'diners club':
            return 14
        else:
            return 16
    
    @staticmethod
    def _get_cvv_length(bin_info):
        """
        Determina la longitud del CVV según el BIN
        """
        brand = bin_info.get('brand', '').lower()
        
        if brand == 'american express':
            return 4
        else:
            return 3
    
    @staticmethod
    def _generate_card_number(prefix, length):
        """
        Genera un número de tarjeta válido usando el algoritmo de Luhn.
        """
        # Reemplazar las 'x' con dígitos aleatorios
        number = list(prefix)
        for i in range(len(number)):
            if number[i].lower() == 'x':
                number[i] = str(random.randint(0, 9))
        
        number = ''.join(number)
        
        # Si el número es más corto que la longitud deseada, agregar dígitos aleatorios
        if len(number) < length - 1:
            number += ''.join([str(random.randint(0, 9)) for _ in range(length - 1 - len(number))])
        # Si el número es más largo, truncarlo (excluyendo el último dígito que será el dígito de verificación)
        elif len(number) >= length:
            number = number[:length - 1]
        
        # Calcular el dígito de verificación usando el algoritmo de Luhn
        check_digit = CreditCardGenerator._luhn_check_digit(number)
        
        return number + str(check_digit)
    
    @staticmethod
    def _luhn_check_digit(number):
        """
        Calcula el dígito de verificación usando el algoritmo de Luhn.
        """
        sum_digits = 0
        
        # Para tarjetas de 16 dígitos, duplicamos los dígitos en posiciones pares (0-based)
        # Para tarjetas de 15 dígitos, duplicamos los dígitos en posiciones impares (0-based)
        for i in range(len(number)):
            digit = int(number[i])
            if (len(number) + 1 == 16 and i % 2 == 0) or (len(number) + 1 == 15 and i % 2 == 1):
                digit *= 2
                if digit > 9:
                    digit -= 9
            sum_digits += digit
        
        # Calcular el dígito de verificación
        check_digit = (10 - (sum_digits % 10)) % 10
        return check_digit
    
    @staticmethod
    def _generate_month(month):
        """
        Genera un mes de vencimiento
        """
        if month.lower() in ('rand', 'rnd'):
            current_month = datetime.now().month
            return str(random.randint(current_month, 12)).zfill(2)
        return month.zfill(2)
    
    @staticmethod
    def _generate_year(year):
        """
        Genera un año de vencimiento
        """
        if year.lower() in ('rand', 'rnd'):
            current_year = datetime.now().year
            return str(random.randint(current_year, current_year + 10))
        return year
    
    @staticmethod
    def _generate_cvv(cvv, length):
        """
        Genera un CVV
        """
        if cvv.lower() in ('rand', 'rnd', 'xxx'):
            return ''.join([str(random.randint(0, 9)) for _ in range(length)])
        return cvv.zfill(length)