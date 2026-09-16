#!/bin/bash
# GitHub Pages Stats Sync — updates /docs/stats with live data daily

set -e

GITHUB_PAGES_DIR="${1:-.../docs/stats}"
STATS_API="${2:-http://localhost:8765/v1/stats}"
REPO_URL="${3:-https://github.com/CorvinLabs/CorvinOS}"

echo "[$(date)] Starting GitHub Pages stats sync..."

mkdir -p "$GITHUB_PAGES_DIR"

# Fetch live stats
echo "Fetching live stats from $STATS_API..."
curl -s "$STATS_API/live?include_geo=true&include_models=true" > "$GITHUB_PAGES_DIR/stats-live.json"
curl -s "$STATS_API/live/geographic" > "$GITHUB_PAGES_DIR/stats-geographic.json"
curl -s "$STATS_API/live/models" > "$GITHUB_PAGES_DIR/stats-models.json"
curl -s "$STATS_API/live/health" > "$GITHUB_PAGES_DIR/stats-health.json"

echo "✅ Stats JSON files updated"

# Generate HTML index
cat > "$GITHUB_PAGES_DIR/index.html" << 'HTML'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CorvinOS Live Stats</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; 
            color: #e2e8f0;
            line-height: 1.6;
        }
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        h1 { margin-bottom: 1rem; color: #f1f5f9; }
        .timestamp { color: #94a3b8; font-size: 0.9rem; margin-bottom: 2rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }
        .card {
            background: #1e293b;
            border-left: 4px solid #60a5fa;
            padding: 1.5rem;
            border-radius: 0.5rem;
        }
        .card.error { border-left-color: #ef4444; }
        .card.success { border-left-color: #10b981; }
        .card.warning { border-left-color: #f59e0b; }
        .card-label { color: #94a3b8; font-size: 0.9rem; margin-bottom: 0.5rem; }
        .card-value { font-size: 2rem; font-weight: bold; color: #60a5fa; }
        .card.error .card-value { color: #ef4444; }
        .card.success .card-value { color: #10b981; }
        .card.warning .card-value { color: #f59e0b; }
        #map { height: 400px; margin: 2rem 0; border-radius: 0.5rem; }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        th, td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #334155; }
        th { background: #0f172a; color: #cbd5e1; font-weight: 600; }
        tr:hover { background: #1e293b; }
        .loading { text-align: center; color: #94a3b8; }
        .error { color: #ef4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌍 CorvinOS Live Stats Dashboard</h1>
        <div class="timestamp" id="timestamp"></div>
        
        <div id="loading" class="loading">Loading live stats...</div>
        <div id="content" style="display: none;">
            <div class="grid" id="summary"></div>
            <h2>Geographic Distribution</h2>
            <div id="map"></div>
            
            <h2>Model Usage</h2>
            <div class="grid" id="models"></div>
            
            <h2>Instances</h2>
            <table id="instances">
                <thead>
                    <tr>
                        <th>Instance</th>
                        <th>Region</th>
                        <th>Requests</th>
                        <th>Error Rate</th>
                        <th>Cost</th>
                        <th>Latency</th>
                        <th>Confidence</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        <div id="error" class="error" style="display: none;"></div>
    </div>

    <script>
        // Initialize Leaflet map
        let map = null;

        async function loadStats() {
            try {
                const [liveRes, geoRes, modelsRes, healthRes] = await Promise.all([
                    fetch('stats-live.json'),
                    fetch('stats-geographic.json'),
                    fetch('stats-models.json'),
                    fetch('stats-health.json')
                ]);

                const live = await liveRes.json();
                const geo = await geoRes.json();
                const models = await modelsRes.json();
                const health = await healthRes.json();

                renderSummary(live.summary);
                renderMap(geo.map_data);
                renderModels(models);
                renderInstances(live.instances);

                document.getElementById('timestamp').textContent = 
                    `Last updated: ${new Date().toLocaleString()}`;
                document.getElementById('loading').style.display = 'none';
                document.getElementById('content').style.display = 'block';
            } catch (error) {
                console.error('Error loading stats:', error);
                document.getElementById('error').textContent = 'Error loading stats. Make sure the API is accessible.';
                document.getElementById('error').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }

        function renderSummary(summary) {
            const html = `
                <div class="card success">
                    <div class="card-label">Total Requests</div>
                    <div class="card-value">${summary.total_requests.toLocaleString()}</div>
                </div>
                <div class="card ${summary.error_rate_pct > 0.1 ? 'error' : 'success'}">
                    <div class="card-label">Error Rate</div>
                    <div class="card-value">${summary.error_rate_pct.toFixed(2)}%</div>
                </div>
                <div class="card warning">
                    <div class="card-label">Total Cost</div>
                    <div class="card-value">$${summary.total_cost_usd.toFixed(0)}</div>
                </div>
                <div class="card success">
                    <div class="card-label">Avg Confidence</div>
                    <div class="card-value">${(summary.avg_confidence * 100).toFixed(1)}%</div>
                </div>
                <div class="card">
                    <div class="card-label">Total Instances</div>
                    <div class="card-value">${summary.total_instances}</div>
                </div>
            `;
            document.getElementById('summary').innerHTML = html;
        }

        function renderMap(mapData) {
            if (!map) {
                map = L.map('map').setView([20, 0], 2);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap contributors',
                    maxZoom: 19
                }).addTo(map);
            }

            mapData.forEach(instance => {
                const color = instance.health === 'healthy' ? '#10b981' : '#f59e0b';
                L.circleMarker([instance.lat, instance.lng], {
                    radius: 8,
                    fillColor: color,
                    color: color,
                    weight: 2,
                    opacity: 0.8,
                    fillOpacity: 0.8
                }).bindPopup(`
                    <strong>${instance.instance_id}</strong><br>
                    Region: ${instance.region}<br>
                    Requests: ${instance.requests.toLocaleString()}<br>
                    Cost: $${instance.cost.toFixed(0)}
                `).addTo(map);
            });
        }

        function renderModels(models) {
            const html = `
                <div class="card">
                    <div class="card-label">Haiku</div>
                    <div class="card-value">${models.distribution_pct.haiku.toFixed(1)}%</div>
                    <small>${models.model_usage.haiku.toLocaleString()} calls</small>
                </div>
                <div class="card">
                    <div class="card-label">Sonnet</div>
                    <div class="card-value">${models.distribution_pct.sonnet.toFixed(1)}%</div>
                    <small>${models.model_usage.sonnet.toLocaleString()} calls</small>
                </div>
                <div class="card">
                    <div class="card-label">Opus</div>
                    <div class="card-value">${models.distribution_pct.opus.toFixed(1)}%</div>
                    <small>${models.model_usage.opus.toLocaleString()} calls</small>
                </div>
            `;
            document.getElementById('models').innerHTML = html;
        }

        function renderInstances(instances) {
            const tbody = document.querySelector('#instances tbody');
            tbody.innerHTML = instances.map(i => `
                <tr>
                    <td>${i.instance_id.split('-').pop()}</td>
                    <td>${i.region}</td>
                    <td>${i.total_requests.toLocaleString()}</td>
                    <td style="color: ${i.error_rate_pct > 0.1 ? '#ef4444' : '#10b981'}">
                        ${i.error_rate_pct.toFixed(2)}%
                    </td>
                    <td>$${i.cost_usd.toFixed(0)}</td>
                    <td>${i.avg_latency_ms.toFixed(0)}ms</td>
                    <td>${(i.learning_confidence * 100).toFixed(1)}%</td>
                </tr>
            `).join('');
        }

        // Load stats on page load
        loadStats();

        // Refresh every 10 seconds
        setInterval(loadStats, 10000);
    </script>
</body>
</html>
HTML

echo "✅ HTML dashboard generated"

# Git commit and push (if in repo)
if [ -d "$GITHUB_PAGES_DIR/.git" ] || git rev-parse --git-dir > /dev/null 2>&1; then
    cd "$GITHUB_PAGES_DIR"
    git add .
    git commit -m "docs(stats): update live stats dashboard [automated]" || true
    git push origin || true
    echo "✅ GitHub Pages updated"
fi

echo "[$(date)] ✅ Stats sync complete"
