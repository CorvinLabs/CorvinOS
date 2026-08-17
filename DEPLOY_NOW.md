# 🚀 Deploy to corvin-labs.com — NOW!

## One Command to Production

```bash
ssh root@corvin-labs.com
bash <(curl -fsSL https://raw.githubusercontent.com/corvinOS/CorvinOS/main/deploy/production-deploy.sh) corvin-labs.com
```

**That's it!** Your dashboard is live in ~3 minutes.

---

## What Gets Deployed

✅ **Real Data** — No mock, uses actual instance metrics  
✅ **SSL/TLS** — Automatic Let's Encrypt certificate  
✅ **Auto-Renewal** — Cert renews automatically every 60 days  
✅ **Nginx** — Reverse proxy with caching & security headers  
✅ **Systemd** — Auto-start on reboot, auto-restart on crash  
✅ **Health Checks** — Built-in monitoring endpoints  

---

## Access Your Dashboard

After deployment completes:

```
https://corvin-labs.com/stats
```

**API Endpoint:**
```
https://corvin-labs.com/api/metrics/stats
```

---

## If DNS Isn't Set Up Yet

Before running the deploy script:

1. **Update DNS** to point to your server:
   ```
   corvin-labs.com A <your-server-ip>
   ```

2. **Wait 5-15 minutes** for DNS to propagate

3. **Test DNS:**
   ```bash
   nslookup corvin-labs.com
   # Should show your server IP
   ```

4. **Then run the deploy script**

---

## Manage the Service

```bash
# Check status
sudo systemctl status corvinos-stats

# View logs
sudo journalctl -u corvinos-stats -f

# Restart
sudo systemctl restart corvinos-stats

# Stop
sudo systemctl stop corvinos-stats
```

---

## Verify It's Working

```bash
# Health check
curl https://corvin-labs.com/health

# API test
curl https://corvin-labs.com/api/metrics/stats | jq .

# Dashboard
curl -I https://corvin-labs.com/stats
# Should return 200 OK
```

---

## If Something Goes Wrong

```bash
# Check service logs
sudo journalctl -u corvinos-stats -n 50

# Check Nginx
sudo systemctl status nginx
sudo nginx -t

# Check SSL certificate
sudo certbot certificates

# Check if ports are open
sudo netstat -tlnp | grep -E ':(80|443)'
```

---

## Configuration Files (if you need to edit)

- **Service:** `/etc/systemd/system/corvinos-stats.service`
- **Nginx:** `/etc/nginx/sites-available/corvinos-stats`
- **App:** `/opt/corvinos-stats/scripts/production-server.py`
- **SSL:** `/etc/letsencrypt/live/corvin-labs.com/`

---

## Monitoring (Optional)

To view metrics in real-time:
```bash
# Stream logs
sudo journalctl -u corvinos-stats -f

# Monitor resource usage
watch ps aux | grep production-server

# Check Nginx cache stats
sudo tail -f /var/log/nginx/access.log
```

---

## That's All!

Your production stats dashboard is now:
- ✅ Live at https://corvin-labs.com/stats
- ✅ Using real instance data (no mock)
- ✅ Protected with SSL/TLS (Let's Encrypt)
- ✅ Auto-renewing certificates
- ✅ Auto-restarting on crashes
- ✅ Auto-starting on reboot
- ✅ Caching API responses for performance
- ✅ Rate-limited (100 req/min per IP)
- ✅ Monitored via systemd/journalctl

**Happy monitoring!** 🚀📊
