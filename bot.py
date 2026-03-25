import os
import subprocess
import logging
import keyring
from typing import List
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv

# Define the service name for Keychain
SERVICE_NAME = "local_ai_bridge"

def get_secret(key, default=None):
    """Retrieve secret from macOS Keychain or environment variable as fallback."""
    # First, try to get from macOS Keychain
    secret = keyring.get_password(SERVICE_NAME, key)
    
    # If not found in Keychain, try the environment (which might be loaded from .env)
    if not secret:
        # Load from .env if it exists
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
        secret = os.getenv(key, default)
    
    return secret

# Configuration
TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
AUTHORIZED_IDS_STR = get_secret("AUTHORIZED_USER_IDS", "")
AUTHORIZED_IDS = [int(i.strip()) for i in AUTHORIZED_IDS_STR.split(",") if i.strip()]
AUTHORIZED_USERNAMES_STR = get_secret("AUTHORIZED_USERNAMES", "")
AUTHORIZED_USERNAMES = [u.strip().lower() for u in AUTHORIZED_USERNAMES_STR.split(",") if u.strip()]
LM_STUDIO_API_URL = get_secret("LM_STUDIO_API_URL", "http://localhost:1234/v1")
LM_STUDIO_MODEL = get_secret("LM_STUDIO_MODEL_NAME", "local-model")

# Initialize Logger
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize LM Studio client (OpenAI-compatible)
lms_client = OpenAI(base_url=LM_STUDIO_API_URL, api_key="lm-studio")

async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if the user is authorized by ID or Username."""
    user = update.effective_user
    user_id = user.id
    username = user.username.lower() if user.username else ""
    
    if user_id not in AUTHORIZED_IDS and username not in AUTHORIZED_USERNAMES:
        logger.warning(f"Unauthorized access attempt by {user_id} (@{username})")
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greeting message."""
    if not await restricted(update, context): return
    await update.message.reply_text(
        "🚀 Local AI Bridge Bot Started!\n\n"
        "Commands:\n"
        "/gemma <prompt> - Chat with LM Studio (local Gemma)\n"
        "/gemini <prompt> - Run Gemini CLI command locally\n"
        "/help - Show this help message"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display help information."""
    if not await restricted(update, context): return
    await start(update, context)

async def gemma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat with the local Gemma model via LM Studio."""
    if not await restricted(update, context): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❓ Please provide a prompt: /gemma <prompt>")
        return

    status_msg = await update.message.reply_text("⏳ Gemma is thinking...")
    
    try:
        completion = lms_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        response = completion.choices[0].message.content
        
        try:
            # Attempt 1: Standard Markdown
            await status_msg.edit_text(response, parse_mode="Markdown")
        except Exception:
            # Fallback: Plain text
            await status_msg.edit_text(response)
    except Exception as e:
        logger.error(f"LM Studio API Error: {str(e)}")
        await status_msg.edit_text(f"❌ Error connecting to LM Studio: {str(e)}")

async def gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute Gemini CLI command locally."""
    if not await restricted(update, context): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❓ Please provide a prompt: /gemini <prompt>")
        return

    status_msg = await update.message.reply_text("⏳ Gemini CLI is processing...")
    
    try:
        # Run Gemini CLI in headless mode
        result = subprocess.run(
            ["gemini", "-p", prompt, "--approval-mode", "plan"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        output = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
        
        if not output:
            output = "(No output received from Gemini CLI)"
            
        if len(output) > 4000:
            output = output[:4000] + "\n\n... (Output truncated)"
            
        await status_msg.edit_text(f"```\n{output}\n```", parse_mode="Markdown")
        
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("❌ Request timed out (60s).")
    except Exception as e:
        logger.error(f"Gemini CLI Execution Error: {str(e)}")
        await status_msg.edit_text(f"❌ Error running Gemini CLI: {str(e)}")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in Keychain or environment.")
        exit(1)
        
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("gemma", gemma))
    app.add_handler(CommandHandler("gemini", gemini))
    
    print("🤖 Local AI Bridge Bot is starting...")
    app.run_polling()
