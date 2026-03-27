import os
import subprocess
import logging
import keyring
from typing import List
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, PicklePersistence
from openai import OpenAI
from dotenv import load_dotenv

import re
import html

import json

# Define the service name for Keychain
SERVICE_NAME = "local_ai_bridge"

def md_to_html(text):
    """Convert basic Markdown to Telegram-compatible HTML."""
    # 1. Escape HTML special characters
    text = html.escape(text)
    
    # 2. Convert bold: **text** -> <b>text</b>
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    
    # 3. Convert code blocks: ```text``` -> <pre>text</pre>
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)
    
    # 4. Convert inline code: `text` -> <code>text</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    # 5. Convert italic: *text* -> <i>text</i> (using a non-greedy match)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    
    return text

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
        "/gemma <prompt> - Chat with local Gemma (persistent sessions)\n"
        "/gemini <prompt> - Chat with Gemini (Flash, persistent sessions)\n"
        "/reset - Start fresh sessions for both AI models\n"
        "/help - Show this help message"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display help information."""
    if not await restricted(update, context): return
    await start(update, context)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset the current Gemini and Gemma sessions."""
    if not await restricted(update, context): return
    context.user_data.pop("gemini_session_id", None)
    context.user_data.pop("gemma_history", None)
    await update.message.reply_text("🔄 AI sessions have been reset.")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to show current Gemma history."""
    if not await restricted(update, context): return
    history = context.user_data.get("gemma_history", [])
    if not history:
        await update.message.reply_text("📜 History is currently empty.")
        return
    
    debug_text = "📜 *Current Gemma History:*\n\n"
    for msg in history:
        role = msg["role"].capitalize()
        content = msg["content"]
        if len(content) > 100:
            content = content[:100] + "..."
        debug_text += f"👤 *{role}*: {content}\n"
    
    await update.message.reply_text(debug_text, parse_mode="Markdown")

async def gemma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat with the local Gemma model via LM Studio with session history."""
    if not await restricted(update, context): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❓ Please provide a prompt: /gemma <prompt>")
        return

    status_msg = await update.message.reply_text("⏳ Gemma is thinking...")
    
    # Session history for Gemma
    history = context.user_data.get("gemma_history", [])
    
    # Add System prompt if it's a new conversation
    if not history:
        history.append({"role": "system", "content": "You are a helpful AI assistant. You have a conversation history provided to you. Use this history to provide context-aware answers. Do NOT say you have no memory, because the memory is being provided to you in this message list."})
    
    history.append({"role": "user", "content": prompt})
    
    logger.info(f"Gemma request from user {update.effective_user.id}. Total messages in request: {len(history)}")
    
    try:
        completion = lms_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=history
        )
        response = completion.choices[0].message.content
        
        if response:
            history.append({"role": "assistant", "content": response})
            # Keep history manageable (e.g., last 20 messages)
            context.user_data["gemma_history"] = history[-20:]
            logger.info(f"Updated history for user {update.effective_user.id}. New length: {len(context.user_data['gemma_history'])}")
        
        if not response:
            response = "(No response received from local AI)"
        
        # Check if the response is too long for Telegram
        if len(response) > 4000:
            response = response[:4000] + "\n\n... (Output truncated due to length)"
        
        try:
            # Attempt 1: Convert Markdown to HTML for reliable bold/italic/code
            html_response = md_to_html(response)
            await status_msg.edit_text(html_response, parse_mode="HTML")
        except Exception:
            try:
                # Attempt 2: Legacy Markdown
                await status_msg.edit_text(response, parse_mode="Markdown")
            except Exception:
                # Final Fallback: Plain text
                await status_msg.edit_text(response)
    except Exception as e:
        logger.error(f"LM Studio API Error: {str(e)}")
        await status_msg.edit_text(f"❌ Error connecting to LM Studio: {str(e)}")

async def gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute Gemini CLI command locally with session persistence."""
    if not await restricted(update, context): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❓ Please provide a prompt: /gemini <prompt>")
        return

    status_msg = await update.message.reply_text("⏳ Gemini (Flash) is processing...")
    
    try:
        # Try to find node and gemini
        node_path = "/opt/homebrew/bin/node"
        if not os.path.exists(node_path):
            node_path = "node"
            
        gemini_path = "/opt/homebrew/bin/gemini"
        if not os.path.exists(gemini_path):
            gemini_path = "gemini"

        # Prepare environment
        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"

        # Base command
        cmd = [node_path, gemini_path, "-m", "flash", "-p", prompt, "--approval-mode", "plan", "--output-format", "json"]
        
        # Session persistence: Check if we have a saved session ID for this user
        session_id = context.user_data.get("gemini_session_id")
        if session_id:
            cmd.extend(["-r", session_id])
            logger.info(f"Resuming Gemini session: {session_id}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120, # Increased timeout for potential tool usage
            env=env
        )
        
        raw_output = result.stdout.strip()
        error_output = result.stderr.strip()
        
        # Try to parse JSON output
        try:
            # The output might contain logs before the actual JSON object
            json_start = raw_output.find('{')
            if json_start != -1:
                json_data = json.loads(raw_output[json_start:])
                response_text = json_data.get("response", "(No response field in JSON)")
                new_session_id = json_data.get("session_id")
                
                if new_session_id:
                    context.user_data["gemini_session_id"] = new_session_id
            else:
                response_text = raw_output if raw_output else error_output
        except Exception as json_err:
            logger.error(f"Failed to parse Gemini JSON: {json_err}")
            response_text = raw_output if raw_output else error_output

        if not response_text:
            response_text = "(No output received from Gemini CLI)"
            
        if len(response_text) > 4000:
            response_text = response_text[:4000] + "\n\n... (Output truncated)"
            
        await status_msg.edit_text(response_text)
        
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("❌ Request timed out (120s).")
    except Exception as e:
        logger.error(f"Gemini CLI Execution Error: {str(e)}")
        await status_msg.edit_text(f"❌ Error running Gemini CLI: {str(e)}")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in Keychain or environment.")
        exit(1)
        
    # Persistent storage for sessions
    persistence = PicklePersistence(filepath="bot_data.pickle")
    
    app = ApplicationBuilder().token(TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("gemma", gemma))
    app.add_handler(CommandHandler("gemini", gemini))
    
    print("🤖 Local AI Bridge Bot is starting...")
    app.run_polling()
