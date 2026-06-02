# Local AI Telegram Bridge 🚀

A secure Telegram bot that bridges user messages to a local **LM Studio** instance (Gemma) and the **Antigravity CLI**.

## 🌟 Features

- **Local AI (Gemma):** Chat with your locally running model via LM Studio's OpenAI-compatible API.
- **Antigravity CLI Integration:** Execute tasks on your local machine using the Antigravity CLI directly from Telegram.
- **macOS Keychain Security:** Sensitive credentials (tokens, IDs) are stored in the macOS Keychain, not in plain text.
- **User-Specific Access:** Restricted by both numeric Telegram ID and username for maximum security.
- **Markdown-to-HTML:** Intelligent formatting that ensures Gemma's responses look great in Telegram every time.
- **Auto-Start & Resilience:** Built-in support for macOS LaunchAgents to ensure the bot is always running.

## 🛠️ Installation

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/kikoso/local-ai-telegram-bridge.git
    cd local-ai-telegram-bridge
    ```

2.  **Setup Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configure Credentials:**
    - Create a `.env` file with your `TELEGRAM_BOT_TOKEN`, `AUTHORIZED_USER_IDS`, `AUTHORIZED_USERNAMES`, `LM_STUDIO_API_URL`, `LM_STUDIO_MODEL_NAME`, and `LM_STUDIO_API_KEY`.
    - Run the migration script to move them to the macOS Keychain:
    ```bash
    python migrate_to_keychain.py
    ```
    - Delete the `.env` file once complete: `rm .env`.

## 🔄 Auto-Start & Resilience

This bot is designed to be **always active**. It uses a macOS **LaunchAgent** to handle persistence:

- **Starts on Login:** The bot launches automatically as soon as you log in to your Mac.
- **Auto-Restart on Crash:** If the bot process ever fails or is killed, macOS will automatically restart it within seconds.
- **Background Execution:** It runs "headless" in the background, requiring no open terminal windows.

**Service Details:**
- **Label:** `com.user.local-ai-bridge`
- **Config:** `~/Library/LaunchAgents/com.user.local-ai-bridge.plist`
- **Logs:** Check `~/local-ai-bridge/bot.log` and `~/local-ai-bridge/bot.err` for output and errors.

## 🤖 Usage

Message your bot on Telegram with the following commands:

- `/gemma <prompt>` — Chat with your local Gemma model (LM Studio).
- `/antigravity <prompt>` — Execute a command via Antigravity CLI (with `/gemini` legacy alias).
- `/reload` — Refresh authorized users and configuration from Keychain.
- `/start` or `/help` — Show available commands.

## 🔒 Security

- **Restricted Access:** The bot only responds to the IDs and usernames specified in your Keychain. Use `/reload` after updating secrets.
- **Input Limits:** Prompts are limited to 2000 characters to ensure stability.
- **ReadOnly Antigravity:** The `/antigravity` command is run in safe planning mode by default. Pass `--auto` to auto-approve tool execution (caution!).
- **Isolated Environment:** Antigravity CLI runs in a sanitized environment to prevent host information leakage.
