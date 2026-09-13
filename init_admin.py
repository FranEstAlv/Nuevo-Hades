from key_manager import KeyManager

def main():
    # Inicializar el gestor de claves
    key_manager = KeyManager(db_file="data/db/keys.db")

    
    # Agregar el primer administrador (reemplaza 123456789 con tu ID de Telegram)
    admin_id = 5531198491  # Reemplaza con tu ID de usuario de Telegram
    
    if key_manager.add_admin(admin_id):
        print(f"Administrador {admin_id} agregado correctamente.")
        print("Ahora puedes usar el bot y ejecutar comandos de administrador como /genkey")
    else:
        print(f"Error al agregar el administrador {admin_id}.")

if __name__ == "__main__":
    main()