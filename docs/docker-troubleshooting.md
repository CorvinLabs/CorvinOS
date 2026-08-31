# Corvin Docker — Troubleshooting Guide

## Container Won't Start

### Check logs

```bash
sudo docker logs -f corvin
sudo docker logs -f corvin-caddy
```

### "Cannot connect to Docker daemon"

**Problem:** `Cannot connect to Docker daemon at unix:///var/run/docker.sock`

**Solution:**
```bash
# Ensure Docker is running
sudo systemctl start docker

# Or add your user to docker group (Linux only)
sudo usermod -aG docker $USER
newgrp docker
```

### "No such image"

**Problem:** `Error response from daemon: image not found`

**Solution:**
```bash
# Pull the image manually
docker pull ghcr.io/veegee82/corvinos:latest

# Or build locally
cd /opt/corvin/repo
docker build -t corvin:dev -f ops/Dockerfile .
```

---

## Healthcheck Failed

### Problem: "unhealthy" status on startup

The container may need 60+ seconds to boot all services. Check logs:

```bash
sudo docker logs corvin | grep -E "(adapter|gateway|bridge-|ERROR)"
```

### If still failing after 2 minutes:

1. **Check entrypoint permissions:**
   ```bash
   sudo docker exec corvin ls -la /home/corvin/.corvin
   # Should show: drwxr-xr-x corvin corvin
   ```

2. **Check Python venv:**
   ```bash
   sudo docker exec corvin /opt/corvin-venv/bin/python --version
   sudo docker exec corvin pip list | grep -E "anthropic|fastapi"
   ```

3. **Check supervisord status:**
   ```bash
   sudo docker exec corvin supervisorctl status
   # Should show: adapter RUNNING, gateway RUNNING
   ```

---

## Web Console Returns 404

### Problem: "Cannot GET /console/"

The console UI is mounted at `/console/` under the gateway. Verify:

```bash
# Check gateway is running
sudo docker exec corvin supervisorctl status gateway

# Check the console UI was built
sudo docker exec corvin ls -la /opt/corvin-repo/core/console/corvin_console/web-next/dist/

# Check gateway can serve it
sudo docker exec corvin curl -I http://localhost:8000/console/ 2>/dev/null | head -5
# Should show: HTTP/1.1 200 OK or similar
```

### Fix: Rebuild the image

If the Next.js build failed during image creation:

```bash
cd /opt/corvin/repo
docker build -t corvin:dev -f ops/Dockerfile . --no-cache
# Update CORVIN_IMAGE in .env to corvin:dev
# Restart: sudo systemctl restart corvin-compose
```

---

## Bridge Daemon Not Starting

### Check which bridges are enabled

```bash
sudo docker exec corvin env | grep CORVIN_BRIDGE
# Should show CORVIN_BRIDGE_DISCORD=true, etc.
```

### Check supervisord status

```bash
sudo docker exec corvin supervisorctl status bridge-discord
# If "FATAL", check the log file
```

### Check bridge logs

```bash
# View the bridge daemon's stderr
sudo docker exec corvin tail /var/log/corvin/bridge-discord.err

# Or stdout (less detail)
sudo docker exec corvin tail /var/log/corvin/bridge-discord.log
```

### Common bridge failures:

| Error | Cause | Fix |
|-------|-------|-----|
| `SyntaxError in settings.json` | Malformed JSON | Validate: `jq . < /opt/corvin/bridge-settings/discord.json` |
| `"token" is not defined` | Missing token field | Add `"discord_token": "..."` to settings.json |
| `whitelist must be an array` | Wrong format | Use `"whitelist": [123, 456]` not `"whitelist": "123,456"` |
| `Cannot find module` | Node deps not installed | `sudo docker exec corvin npm ls -g discord.js` |
| `EACCES: permission denied` | File ownership wrong | `sudo chown -R 1000:1000 /opt/corvin/home` |

### Validate bridge settings

```bash
# Check JSON syntax
sudo jq . /opt/corvin/bridge-settings/discord.json

# Check required fields (example for Discord)
sudo jq .discord_token /opt/corvin/bridge-settings/discord.json
sudo jq .whitelist /opt/corvin/bridge-settings/discord.json
```

---

## TLS / HTTPS Issues

### Certificate not renewing

**Problem:** "Certificate for `corvin.example.com` expired"

**Check Caddy logs:**
```bash
sudo docker logs corvin-caddy | grep -i acme
```

**Common causes:**
1. DNS not resolving to this host
2. Port 80 blocked by firewall (ACME needs HTTP-01 challenge)
3. Cloudflare proxy enabled (see ops/README.md for DNS-01 workaround)

**Manual fix:**
```bash
# Force cert renewal
sudo docker exec corvin-caddy caddy reload --config /etc/caddy/Caddyfile

# Or restart Caddy
sudo docker compose -f /opt/corvin/docker-compose.yml restart caddy
```

### Self-signed cert warnings

**Problem:** Browser shows "Your connection is not private"

**This is normal for self-signed certs.** Either:

1. **Add exception in browser** (development only)
2. **Switch to Let's Encrypt** (set `CORVIN_DOMAIN` in .env)
3. **Use SSH tunnel** instead:
   ```bash
   ssh -L 8765:localhost:8765 user@host
   # Then access: http://localhost:8765/console/ (unencrypted locally)
   ```

### "Failed to verify certificate"

**Problem:** CURL or API calls fail with cert verification error

**Fix (development only):**
```bash
# Disable cert verification (NOT for production)
curl -k https://corvin.example.com/console/

# Or add the self-signed cert to your system:
sudo cp /opt/corvin/tls/cert.pem /usr/local/share/ca-certificates/corvin.crt
sudo update-ca-certificates
```

---

## Performance Issues

### High memory usage

**Check container memory:**
```bash
sudo docker stats corvin --no-stream
# Memory column shows current usage
```

**If exceeding `CORVIN_MEM_LIMIT`:**

1. **Increase limit in .env:**
   ```bash
   CORVIN_MEM_LIMIT=8G
   # Then: sudo systemctl restart corvin-compose
   ```

2. **Or find memory-heavy processes:**
   ```bash
   sudo docker exec corvin ps aux --sort=-%mem | head -10
   ```

### Slow response times

**Check logs for errors:**
```bash
sudo docker logs corvin | grep -i "error\|timeout\|slow"
```

**Check if Claude API is responsive:**
```bash
# Test the adapter's connection to Claude
sudo docker exec corvin tail /var/log/corvin/adapter.log | grep -i anthropic
```

**Measure network latency:**
```bash
sudo docker exec corvin curl -w "%{time_total}s\n" -o /dev/null -s https://api.anthropic.com/status
```

---

## Audit Chain Verification Failed

### Problem: "audit.jsonl hash chain is broken"

This is **critical** — indicates tampering or corruption.

**Check manually:**
```bash
sudo docker exec corvin \
  /opt/corvin-repo/operator/voice/scripts/voice-audit.py verify
```

**Common causes:**
1. **Manual edit of audit.jsonl** (never do this)
2. **Abrupt container kill** mid-write (rare)
3. **Filesystem corruption** (check `dmesg`, `fsck`)

**Recovery:**
1. Back up the current audit chain:
   ```bash
   sudo cp /opt/corvin/home/.corvin/tenants/_default/global/audit.jsonl \
           /opt/corvin/backups/audit-corrupted-$(date +%s).jsonl
   ```

2. Report the issue with the corrupted chain attached:
   ```bash
   # https://github.com/veegee82/Corvin/issues
   ```

---

## Storage / Bind-Mount Issues

### "Read-only file system"

**Problem:** "Error writing to /home/corvin: Read-only file system"

**Check mount status:**
```bash
sudo docker exec corvin df /home/corvin
# Should show: Filesystem ... Type rw
```

**If read-only:**
```bash
# Check host mount
mount | grep /opt/corvin/home

# Remount as read-write
sudo mount -o remount,rw /opt/corvin/home
```

### Disk full

**Problem:** "No space left on device"

**Check usage:**
```bash
du -sh /opt/corvin/home
df -h /opt/corvin/

# Check container logs
sudo docker exec corvin du -sh /var/log/corvin
```

**Cleanup:**
```bash
# Rotate old logs
sudo rm -f /opt/corvin/home/.corvin/tenants/_default/global/audit.jsonl.* 

# Or purge old sessions (careful!)
sudo find /opt/corvin/home/.corvin/tenants/_default/sessions -mtime +30 -type d -exec rm -rf {} \;
```

---

## Database / State Corruption

### "SQLite database is locked"

**Problem:** Multiple processes trying to write to audit.jsonl simultaneously

**This shouldn't happen — report it:**
```bash
# Get diagnostics
sudo docker exec corvin lsof /home/corvin/.corvin/tenants/_default/global/audit.jsonl
# Check adapter logs
sudo docker logs corvin | tail -20
```

---

## Networking / Firewall

### Can't reach console from outside

**Check firewall:**
```bash
# UFW (Ubuntu)
sudo ufw status
# Should show: 80/tcp and 443/tcp ALLOW

# iptables (generic)
sudo iptables -L -n | grep -E "80|443"
```

**Check port binding:**
```bash
sudo netstat -tlnp | grep -E ":80|:443"
# Should show: caddy listening on 0.0.0.0:80 and 0.0.0.0:443
```

**Open ports:**
```bash
# UFW
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload

# Or iptables
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

### Cannot reach console on LAN

**Use Tailscale instead** (see ops/tailscale/README.md for simpler setup).

Or test connectivity:

```bash
# From another machine on the LAN
curl -k https://corvin.local:443/console/

# If that fails, check this host's LAN IP
hostname -I
# Then try: curl -k https://192.168.1.100/console/
```

---

## Service Management

### Restart everything

```bash
sudo systemctl restart corvin-compose
# Wait 30-60 seconds for services to boot
sudo docker logs -f corvin | grep -E "RUNNING|ERROR"
```

### Update to latest version

```bash
cd /opt/corvin/repo
sudo git fetch --tags
sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)
sudo systemctl restart corvin-compose
```

### View systemd logs

```bash
sudo journalctl -u corvin-compose -f
```

### Completely restart from scratch

```bash
# Stop the stack
sudo systemctl stop corvin-compose

# Wipe container + volumes (WARNING: deletes data!)
sudo docker compose -f /opt/corvin/docker-compose.yml down -v

# Clear Docker cache
sudo docker system prune -a --volumes

# Restart
sudo systemctl start corvin-compose
```

---

## Still Stuck?

1. **Collect diagnostics:**
   ```bash
   mkdir /tmp/corvin-debug
   sudo docker logs corvin > /tmp/corvin-debug/corvin.log
   sudo docker logs corvin-caddy > /tmp/corvin-debug/caddy.log
   sudo docker exec corvin supervisorctl status > /tmp/corvin-debug/supervisord.status
   sudo docker exec corvin env > /tmp/corvin-debug/env.txt
   sudo docker exec corvin df -h > /tmp/corvin-debug/df.txt
   sudo tar -czf /tmp/corvin-debug.tgz /tmp/corvin-debug/
   ```

2. **Open an issue:**
   - https://github.com/veegee82/Corvin/issues
   - Attach `/tmp/corvin-debug.tgz`
   - Include: OS (Ubuntu 22.04?), Docker version, host RAM/disk

3. **Try the FAQ:**
   - https://github.com/veegee82/Corvin/discussions
