# Test suites reference

Run **all suites** before committing changes to `adapter.py`, any `daemon.js`,
or `shared/js/`:

```bash
bash operator/bridges/run-all-tests.sh
```

Key suites included:

```bash
# Adapter (Python):
python3 operator/bridges/shared/test_adapter_parallel.py
python3 operator/bridges/shared/test_adapter_profiles.py
python3 operator/bridges/shared/test_adapter_cowork.py
python3 operator/bridges/shared/test_router.py
python3 operator/bridges/shared/test_adapter_btw.py
python3 operator/bridges/shared/test_adapter_stream_idle.py
python3 operator/bridges/shared/test_adapter_http_reset.py
python3 operator/bridges/shared/test_adapter_security_hardening.py
python3 operator/bridges/shared/test_consent_gate.py
python3 operator/forge/tests/test_secret_injection.py

# Cowork:
python3 operator/cowork/test/test_resolver.py

# Voice pipeline:
python3 operator/voice/scripts/test_summarize.py
bash    operator/voice/scripts/test_voice_env_lookup.sh

# Bridge runtime (Node):
node operator/bridges/shared/js/test_modules.js
node operator/bridges/shared/js/test_in_chat_commands.js
node operator/bridges/shared/js/test_consent_dispatcher.js

# Daemon boot:
bash operator/bridges/test_daemon_boot.sh
```

(WhatsApp daemon excluded from boot test — verify manually via `bridge.sh restart`.)
