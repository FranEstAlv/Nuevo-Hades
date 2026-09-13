# admin_manager.py
import sys
import os
from key_manager import KeyManager

def main():
    # Inicializar el gestor de claves
    key_manager = KeyManager(db_file="/data/database/keys.db")


    
    # Verificar si ya existen administradores
    admins = key_manager.get_admins()
    
    if not admins:
        print("No hay administradores registrados. Debes agregar al menos uno.")
        print("Usa: python admin_manager.py add_admin <user_id>")
        return
    
    # Procesar comandos
    if len(sys.argv) < 2:
        print("Comandos disponibles:")
        print("  add_admin <user_id> - Agrega un nuevo administrador")
        print("  remove_admin <user_id> - Elimina un administrador")
        print("  list_admins - Lista todos los administradores")
        print("  generate_key <user_id> <plan> <dias> - Genera una clave para un usuario")
        print("  list_active - Lista todos los usuarios con claves activas")
        print("  revoke <user_id> - Revoca la clave de un usuario")
        print("  extend <user_id> <dias> - Extiende la validez de una clave")
        return
    
    command = sys.argv[1]
    
    if command == "add_admin" and len(sys.argv) >= 3:
        user_id = int(sys.argv[2])
        if key_manager.add_admin(user_id):
            print(f"Administrador {user_id} agregado correctamente.")
        else:
            print(f"Error al agregar el administrador {user_id}.")
    
    elif command == "remove_admin" and len(sys.argv) >= 3:
        user_id = int(sys.argv[2])
        if key_manager.remove_admin(user_id):
            print(f"Administrador {user_id} eliminado correctamente.")
        else:
            print(f"Error al eliminar el administrador {user_id} o no existía.")
    
    elif command == "list_admins":
        admins = key_manager.get_admins()
        print("Administradores registrados:")
        for admin_id in admins:
            print(f"  - {admin_id}")
    
    elif command == "generate_key" and len(sys.argv) >= 5:
        user_id = int(sys.argv[2])
        plan = sys.argv[3]
        days = int(sys.argv[4])
        key = key_manager.generate_key(user_id, plan, days, user_id)
        print(f"Clave generada para el usuario {user_id} válida por {days} días (plan {plan}):")
        print(f"  {key}")
    
    elif command == "list_active":
        users = key_manager.get_all_active_users()
        print("Usuarios con claves activas:")
        for user in users:
            status = "Expirada" if user["is_expired"] else f"Activa ({user['days_remaining']} días restantes)"
            print(f"  - Usuario {user['user_id']}: {user['key']} - {status}")
    
    elif command == "revoke" and len(sys.argv) >= 3:
        user_id = int(sys.argv[2])
        if key_manager.revoke_key(user_id):
            print(f"Clave del usuario {user_id} revocada correctamente.")
        else:
            print(f"Error al revocar la clave del usuario {user_id} o no tenía clave activa.")
    
    elif command == "extend" and len(sys.argv) >= 4:
        user_id = int(sys.argv[2])
        days = int(sys.argv[3])
        success, message = key_manager.extend_key(user_id, days)
        if success:
            print(message)
        else:
            print(f"Error: {message}")
    
    else:
        print("Comando no reconocido o argumentos insuficientes.")

if __name__ == "__main__":
    main()
