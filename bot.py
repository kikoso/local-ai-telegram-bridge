import os
import subprocess
import logging
import keyring
from typing import List
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, PicklePersistence
from openai import OpenAI
from dotenv import load_dotenv

import re
import html

import json

# Define the service name for Keychain
SERVICE_NAME = "local_ai_bridge"

# Load .env once at startup as a fallback
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

def md_to_html(text):
    """
    Convert Markdown to Telegram-compatible HTML.
    Handles bold, italic, code blocks, inline code, links, and headers.
    """
    # 1. Escape HTML special characters
    text = html.escape(text)
    
    # 2. Protect code blocks and inline code from other regexes
    code_elements = []
    def save_code(match):
        code_elements.append(match.group(0))
        return f"<!--CODE_ELEMENT_{len(code_elements)-1}-->"
    
    # Protect triple backticks (code blocks)
    text = re.sub(r'```(?:\w+)?\n?(.*?)```', save_code, text, flags=re.DOTALL)
    # Protect single backticks (inline code)
    text = re.sub(r'`([^`]+)`', save_code, text)

    # 3. Apply formatting to the rest of the text
    # Headers (convert to bold)
    text = re.sub(r'^#+\s+(.*)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    
    # List items: * item -> • item
    text = re.sub(r'^\s*[\*\-]\s+', r'• ', text, flags=re.MULTILINE)

    # Bold: **text** or __text__
    text = re.sub(r'(\*\*|__)(.*?)\1', r'<b>\2</b>', text, flags=re.DOTALL)
    
    # Italic: *text* or _text_
    # Note: We use [^\n] to ensure it doesn't match across lines, which breaks on lists
    text = re.sub(r'(?<!\w)(?<!\\)([*_])([^\n]+?)\1(?!\w)', r'<i>\2</i>', text)
    
    # Links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 4. Restore protected code elements
    def restore_code(match):
        idx = int(match.group(1))
        content = code_elements[idx]
        if content.startswith('```'):
            # Extract content from block, removing backticks and optional language tag
            inner = re.sub(r'```(?:\w+)?\n?(.*?)```', r'\1', content, flags=re.DOTALL)
            return f"<pre>{inner}</pre>"
        else:
            # Extract content from inline code
            inner = re.sub(r'`([^`]+)`', r'\1', content)
            return f"<code>{inner}</code>"

    text = re.sub(r'<!--CODE_ELEMENT_(\d+)-->', restore_code, text)
    
    return text

async def send_formatted_message(status_msg, response_text):
    """Send or edit a message with markdown-to-html formatting and fallbacks."""
    try:
        # Attempt 1: HTML parse mode with improved conversion
        html_response = md_to_html(response_text)
        await status_msg.edit_text(html_response, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"HTML parse mode failed, falling back to plain text. Error: {str(e)}")
        # Final Fallback: Plain text (Markdown V1 is too unstable for AI output)
        try:
            await status_msg.edit_text(response_text)
        except Exception as e2:
            logger.error(f"Final fallback failed: {str(e2)}")

def get_secret(key, default=None):
    """Retrieve secret from macOS Keychain or environment variable as fallback."""
    # First, try to get from macOS Keychain
    secret = keyring.get_password(SERVICE_NAME, key)
    
    # If not found in Keychain, try the environment (might be already loaded from .env)
    if secret is None:
        secret = os.getenv(key, default)
    
    return secret

# Configuration
TOKEN = get_secret("TELEGRAM_BOT_TOKEN")

# Initialize Logger
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def load_config():
    """Load or reload configuration from secrets."""
    global AUTHORIZED_IDS, AUTHORIZED_USERNAMES, LM_STUDIO_API_URL, LM_STUDIO_MODEL, LM_STUDIO_API_KEY, lms_client
    
    AUTHORIZED_IDS_STR = get_secret("AUTHORIZED_USER_IDS", "")
    AUTHORIZED_IDS = [int(i.strip()) for i in AUTHORIZED_IDS_STR.split(",") if i.strip()]
    
    AUTHORIZED_USERNAMES_STR = get_secret("AUTHORIZED_USERNAMES", "")
    AUTHORIZED_USERNAMES = [u.strip().lower() for u in AUTHORIZED_USERNAMES_STR.split(",") if u.strip()]
    
    LM_STUDIO_API_URL = get_secret("LM_STUDIO_API_URL", "http://localhost:1234/v1")
    LM_STUDIO_MODEL = get_secret("LM_STUDIO_MODEL_NAME", "local-model")
    LM_STUDIO_API_KEY = get_secret("LM_STUDIO_API_KEY", "lm-studio")
    
    # Re-initialize client if needed
    lms_client = OpenAI(base_url=LM_STUDIO_API_URL, api_key=LM_STUDIO_API_KEY, timeout=300.0, max_retries=3)
    logger.info("Configuration loaded/reloaded.")

# Initial load
load_config()

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

async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reload configuration from Keychain/Env."""
    if not await restricted(update, context): return
    load_config()
    await update.message.reply_text("✅ Configuration reloaded successfully.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greeting message."""
    if not await restricted(update, context): return
    
    current_model = context.user_data.get("preferred_model", "None (use commands)")
    
    await update.message.reply_text(
        "🚀 Local AI Bridge Bot Started!\n\n"
        f"Current Preferred Model: <b>{current_model}</b>\n\n"
        "Commands:\n"
        "/set &lt;model&gt; - Set preferred model (gemini or gemma) for direct messages\n"
        "/gemma &lt;prompt&gt; - Chat with local Gemma (persistent sessions)\n"
        "/gemini [--auto] &lt;prompt&gt; - Chat with Gemini (Flash, persistent sessions)\n"
        "  - Use --auto to allow Gemini to execute shell commands (CAUTION)\n"
        "/reset - Start fresh sessions for both AI models\n"
        "/reload - Refresh configuration from Keychain\n"
        "/help - Show this help message\n\n"
        "💡 You can just type your message directly if you have a preferred model set!",
        parse_mode="HTML"
    )

async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the preferred model for direct messages."""
    if not await restricted(update, context): return
    
    if not context.args:
        await update.message.reply_text("❓ Usage: /set <gemini|gemma>")
        return
        
    model = context.args[0].lower()
    if model not in ["gemini", "gemma"]:
        await update.message.reply_text("❌ Invalid model. Use 'gemini' or 'gemma'.")
        return
        
    context.user_data["preferred_model"] = model
    await update.message.reply_text(f"✅ Preferred model set to: <b>{model}</b>", parse_mode="HTML")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct text messages by routing to the preferred model."""
    if not await restricted(update, context): return
    
    # Ignore if it's a command (already handled by CommandHandlers)
    if update.message.text.startswith('/'):
        return

    preferred_model = context.user_data.get("preferred_model")
    if not preferred_model:
        await update.message.reply_text("ℹ️ No preferred model set. Use /set <model> or use /gemini or /gemma commands.")
        return

    # Prepare context.args as if it was a command
    context.args = update.message.text.split()
    
    if preferred_model == "gemini":
        await gemini(update, context)
    elif preferred_model == "gemma":
        await gemma(update, context)

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
    
    debug_text = "📜 # Current Gemma History\n\n"
    for msg in history:
        role = msg["role"].capitalize()
        content = msg["content"]
        if len(content) > 100:
            content = content[:100] + "..."
        debug_text += f"👤 **{role}**: {content}\n"
    
    html_debug = md_to_html(debug_text)
    await update.message.reply_text(html_debug, parse_mode="HTML")

def ensure_alternating_roles(messages: List[dict]) -> List[dict]:
    """Ensure that message roles alternate between user and assistant."""
    if not messages:
        return []
    
    new_messages = []
    for msg in messages:
        if not new_messages:
            new_messages.append(msg.copy())
            continue
        
        last_msg = new_messages[-1]
        # Only merge if it's the same role and not a system message (though system usually only appears once at start)
        if last_msg["role"] == msg["role"] and msg["role"] != "system":
            last_msg["content"] += "\n\n" + msg["content"]
        else:
            new_messages.append(msg.copy())
    
    return new_messages

async def gemma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat with the local Gemma model via LM Studio with session history."""
    if not await restricted(update, context): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text(f"❓ Please provide a prompt: /{update.message.text.split()[0][1:]} <prompt>")
        return
        
    if len(prompt) > 2000:
        await update.message.reply_text("❌ Prompt too long (max 2000 chars).")
        return

    status_msg = await update.message.reply_text("⏳ Gemma is thinking...")
    
    # Session history for Gemma - work on a COPY to avoid corrupting history on failure
    stored_history = context.user_data.get("gemma_history", [])
    history = list(stored_history)
    
    # Add System prompt if it's a new conversation
    if not history:
        history.append({"role": "system", "content": "You are a helpful AI assistant. You have a conversation history provided to you. Use this history to provide context-aware answers. Do NOT say you have no memory, because the memory is being provided to you in this message list."})
    
    history.append({"role": "user", "content": prompt})
    
    # Normalize history for LM Studio (some models require strict alternation)
    normalized_history = ensure_alternating_roles(history)
    
    logger.info(f"Gemma request from user {update.effective_user.id}. Total messages in request: {len(normalized_history)}")
    
    try:
        completion = lms_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=normalized_history
        )
        response = completion.choices[0].message.content
        
        if response:
            # On success, we officially add both the prompt and response to the history
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
            await send_formatted_message(status_msg, response)
        except Exception as send_err:
            logger.error(f"Failed to send/edit message: {send_err}")
    except Exception as e:
        error_detail = str(e)
        if "Read timed out" in error_detail:
            logger.error(f"LM Studio Timeout: {error_detail}")
            await status_msg.edit_text("❌ Error connecting to LM Studio: Read Timed Out.\n\n"
                                     "💡 LM Studio is taking too long to respond. "
                                     "This usually happens if the model is still loading or your machine is busy. "
                                     "Try again in a few seconds.")
        else:
            logger.error(f"LM Studio API Error: {error_detail}")
            await status_msg.edit_text(f"❌ Error connecting to LM Studio: {error_detail}")

async def gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute Gemini CLI command locally with session persistence."""
    if not await restricted(update, context): return
    
    args = list(context.args)
    approval_mode = "plan"
    
    if args and args[0] == "--auto":
        approval_mode = "auto"
        args.pop(0)
        
    prompt = " ".join(args)
    if not prompt:
        await update.message.reply_text(f"❓ Please provide a prompt: /{update.message.text.split()[0][1:]} [--auto] <prompt>")
        return
        
    if len(prompt) > 2000:
        await update.message.reply_text("❌ Prompt too long (max 2000 chars).")
        return

    status_text = "⏳ Gemini (Flash) is processing..."
    if approval_mode == "auto":
        status_text = "⚠️ Gemini (Flash) is processing in AUTO mode..."
        
    status_msg = await update.message.reply_text(status_text)
    
    try:
        # Try to find node and gemini
        node_path = "/opt/homebrew/bin/node"
        if not os.path.exists(node_path):
            node_path = "node"
            
        gemini_path = "/opt/homebrew/bin/gemini"
        if not os.path.exists(gemini_path):
            gemini_path = "gemini"

        # Prepare a clean environment for subprocess
        clean_env = {
            "PATH": f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{os.getenv('PATH', '')}",
            "HOME": os.path.expanduser("~"),
            "LANG": os.getenv("LANG", "en_US.UTF-8"),
            "SHELL": os.getenv("SHELL", "/bin/bash")
        }
        
        # Add any GOOGLE_API_KEY if present in current env
        if os.getenv("GOOGLE_API_KEY"):
            clean_env["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")

        # Base command
        cmd = [node_path, gemini_path, "-m", "flash", "-p", prompt, "--approval-mode", approval_mode, "--output-format", "json"]
        
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
            env=clean_env
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
            
        try:
            await send_formatted_message(status_msg, response_text)
        except Exception as send_err:
            logger.error(f"Failed to send/edit message: {send_err}")
        
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
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("set", set_model))
    app.add_handler(CommandHandler("gemma", gemma))
    app.add_handler(CommandHandler("gemini", gemini))
    
    # Handle direct text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Local AI Bridge Bot is starting...")
    app.run_polling()
