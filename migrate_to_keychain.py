import os
import keyring
from dotenv import load_dotenv

# Path to the .env file
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

# Define the service name for Keychain
SERVICE_NAME = "local_ai_bridge"

def migrate():
    """Migrate secrets from .env to the macOS Keychain."""
    if not os.path.exists(ENV_FILE):
        print("❌ .env file not found. Have you already migrated?")
        return

    print("🚀 Starting migration of secrets to Keychain...")
    load_dotenv(ENV_FILE)

    secrets = [
        "TELEGRAM_BOT_TOKEN",
        "AUTHORIZED_USER_IDS",
        "AUTHORIZED_USERNAMES",
        "LM_STUDIO_API_URL",
        "LM_STUDIO_MODEL_NAME",
        "LM_STUDIO_API_KEY"
    ]

    for secret in secrets:
        value = os.getenv(secret)
        if value:
            # Store secret in macOS Keychain
            keyring.set_password(SERVICE_NAME, secret, value)
            print(f"✅ Successfully migrated: {secret}")
        else:
            print(f"⚠️  Warning: {secret} not found in .env")

    print("\n🎉 Migration complete! You can now delete your .env file.")
    print("Run the bot with the updated script that uses 'keyring' to verify.")

if __name__ == "__main__":
    migrate()
