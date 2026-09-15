# Test suites reference

Run **all suites** before committing changes to `adapter.py`, any `daemon.js`,
or `shared/js/`:

```bash
bash corvin_operator/bridges/run-all-tests.sh
```

Key suites included:

```bash
# Adapter (Python):
python3 corvin_operator/bridges/shared/test_adapter_parallel.py
python3 corvin_operator/bridges/shared/test_adapter_profiles.py
python3 corvin_operator/bridges/shared/test_adapter_cowork.py
python3 corvin_operator/bridges/shared/test_router.py
python3 corvin_operator/bridges/shared/test_adapter_btw.py
python3 corvin_operator/bridges/shared/test_adapter_stream_idle.py
python3 corvin_operator/bridges/shared/test_adapter_http_reset.py
python3 corvin_operator/bridges/shared/test_adapter_security_hardening.py
python3 corvin_operator/bridges/shared/test_consent_gate.py
python3 corvin_operator/forge/tests/test_secret_injection.py

# Cowork:
python3 corvin_operator/cowork/test/test_resolver.py

# Voice pipeline:
python3 corvin_operator/voice/scripts/test_summarize.py
bash    corvin_operator/voice/scripts/test_voice_env_lookup.sh

# Bridge runtime (Node):
node corvin_operator/bridges/shared/js/test_modules.js
node corvin_operator/bridges/shared/js/test_in_chat_commands.js
node corvin_operator/bridges/shared/js/test_consent_dispatcher.js

# Daemon boot:
bash corvin_operator/bridges/test_daemon_boot.sh
```

(WhatsApp daemon excluded from boot test — verify manually via `bridge.sh restart`.)
