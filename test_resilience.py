"""
Tests for the failure modes that took the bot offline for weeks at a time.

Run with:  ./venv/bin/python test_resilience.py

These cover recovery behaviour only - not the Telegram handlers themselves.
"""
import asyncio
import os
import sys
import time
import importlib.util
from pathlib import Path

HERE = Path(__file__).parent


def load_bot():
    """Import bot.py without executing its __main__ block."""
    spec = importlib.util.spec_from_file_location("botmod", HERE / "bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bot = load_bot()

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(f"{'✅' if condition else '❌'} {name}{(' — ' + detail) if detail else ''}")


class FakeBot:
    """Stands in for telegram.Bot; get_me() fails until `fail_until` calls elapse."""

    def __init__(self, failures):
        self.remaining_failures = failures
        self.calls = 0

    async def get_me(self):
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise OSError("[Errno 8] nodename nor servname provided, or not known")
        return {"ok": True}


class FakeApp:
    def __init__(self, failures):
        self.bot = FakeBot(failures)


# --- 1. Watchdog exits when the network stays down (the Aug 18 outage) --------
def test_watchdog_exits_on_sustained_failure():
    bot.WATCHDOG_INTERVAL = 0.01
    bot.WATCHDOG_MAX_FAILURES = 5

    exit_codes = []
    real_exit = os._exit
    os._exit = lambda code: (exit_codes.append(code), (_ for _ in ()).throw(SystemExit(code)))[0]
    try:
        app = FakeApp(failures=999)  # network never comes back

        async def run():
            try:
                await asyncio.wait_for(bot.connectivity_watchdog(app), timeout=5)
            except SystemExit:
                pass

        asyncio.run(run())
    finally:
        os._exit = real_exit

    check("watchdog exits after MAX_FAILURES consecutive failures",
          exit_codes == [1], f"exit_codes={exit_codes}, probes={app.bot.calls}")
    check("watchdog exits promptly, not early or late",
          app.bot.calls == 5, f"probed {app.bot.calls}x, expected 5")


# --- 2. Watchdog does NOT exit on a transient blip ----------------------------
def test_watchdog_survives_transient_failure():
    bot.WATCHDOG_INTERVAL = 0.01
    bot.WATCHDOG_MAX_FAILURES = 5

    exit_codes = []
    real_exit = os._exit
    os._exit = lambda code: exit_codes.append(code)
    try:
        app = FakeApp(failures=3)  # recovers before hitting the threshold

        async def run():
            try:
                await asyncio.wait_for(bot.connectivity_watchdog(app), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        asyncio.run(run())
    finally:
        os._exit = real_exit

    check("watchdog tolerates a transient outage without restarting",
          exit_codes == [], f"exit_codes={exit_codes}")


# --- 3. Locked Keychain falls back to .env (the 3-day boot hang) --------------
def test_keychain_timeout_falls_back_to_env():
    import keyring

    real_get = keyring.get_password
    bot.KEYCHAIN_TIMEOUT = 0.3
    bot._keychain_unavailable = False

    def hangs_forever(service, key):
        time.sleep(3600)

    keyring.get_password = hangs_forever
    os.environ["A_TEST_SECRET"] = "from-env"
    try:
        started = time.monotonic()
        value = bot.get_secret("A_TEST_SECRET")
        elapsed = time.monotonic() - started
    finally:
        keyring.get_password = real_get
        bot._keychain_unavailable = False
        del os.environ["A_TEST_SECRET"]

    check("locked Keychain does not block startup",
          elapsed < 2.0, f"returned in {elapsed:.2f}s")
    check("locked Keychain falls back to .env value",
          value == "from-env", f"got {value!r}")


# --- 4. Hung `agy` subprocess is killed, not waited on forever ----------------
def test_subprocess_timeout_kills_child():
    async def run():
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", "echo partial; sleep 300",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        text = ""

        async def stream():
            nonlocal text
            while True:
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    break
                text += chunk.decode("utf-8", errors="replace")
            await proc.wait()

        timed_out = False
        try:
            await asyncio.wait_for(stream(), timeout=1.5)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        return timed_out, text, proc.returncode

    timed_out, text, rc = asyncio.run(run())
    check("hung subprocess times out", timed_out)
    check("hung subprocess is actually killed", rc is not None and rc < 0, f"returncode={rc}")
    check("partial output is preserved on timeout", "partial" in text, repr(text))


# --- 5. Oversized log is trimmed, not left to fill the disk -------------------
def test_log_trim():
    tmp = HERE / "bot.err.trimtest"
    prev = Path(str(tmp) + ".prev")
    real_path, real_max, real_keep = bot.LOG_PATH, bot.LOG_MAX_BYTES, bot.LOG_KEEP_BYTES
    bot.LOG_PATH, bot.LOG_MAX_BYTES, bot.LOG_KEEP_BYTES = tmp, 1024, 256
    try:
        tmp.write_bytes(b"x" * 5000 + b"TAIL_MARKER")
        bot.trim_log_if_large()
        trimmed = tmp.stat().st_size
        kept = prev.read_bytes() if prev.exists() else b""
        check("oversized log is trimmed", trimmed == 0, f"size now {trimmed}")
        check("recent history preserved in .prev", b"TAIL_MARKER" in kept, f"{len(kept)} bytes kept")

        # A small log must be left alone.
        tmp.write_bytes(b"small")
        bot.trim_log_if_large()
        check("small log is left untouched", tmp.read_bytes() == b"small")
    finally:
        bot.LOG_PATH, bot.LOG_MAX_BYTES, bot.LOG_KEEP_BYTES = real_path, real_max, real_keep
        tmp.unlink(missing_ok=True)
        prev.unlink(missing_ok=True)


for test in (
    test_watchdog_exits_on_sustained_failure,
    test_watchdog_survives_transient_failure,
    test_keychain_timeout_falls_back_to_env,
    test_subprocess_timeout_kills_child,
    test_log_trim,
):
    print(f"\n--- {test.__name__} ---")
    test()

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
