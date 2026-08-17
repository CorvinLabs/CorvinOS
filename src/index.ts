/**
 * CorvinOS Stats Dashboard — Cloudflare Worker
 * Serves dashboard UI + API with real instance metrics
 */

// Mock data (replace with real data from Durable Objects or KV)
const getMockStats = () => ({
  timestamp: new Date().toISOString(),
  cluster: {
    instance_count: 3,
    total_turns: Math.floor(Math.random() * 1000) + 3000,
    total_tokens: Math.floor(Math.random() * 1000) + 29000,
    avg_tokens_per_turn: 10,
    avg_savings_percent: 25.5,
    instances: [
      {
        instance_id: "prod-us-east-1",
        hostname: "prod.us-east-1.corvin.local",
        location: "40.7128,-74.0060", // NYC
        turn_count: 1250 + Math.floor(Math.random() * 10),
        total_tokens: 12500 + Math.floor(Math.random() * 100),
        savings_percent: 28.5,
      },
      {
        instance_id: "prod-eu-west-1",
        hostname: "prod.eu-west-1.corvin.local",
        location: "51.5074,-0.1278", // London
        turn_count: 980 + Math.floor(Math.random() * 10),
        total_tokens: 9800 + Math.floor(Math.random() * 100),
        savings_percent: 25.2,
      },
      {
        instance_id: "prod-ap-southeast-1",
        hostname: "prod.ap-southeast-1.corvin.local",
        location: "-33.8688,151.2093", // Sydney
        turn_count: 756 + Math.floor(Math.random() * 10),
        total_tokens: 7560 + Math.floor(Math.random() * 100),
        savings_percent: 22.8,
      },
    ],
    summary: {
      instance_count: 3,
      total_turns: 2986,
      total_tokens: 29860,
      avg_tokens_per_turn: 10,
      avg_savings_percent: 25.5,
    },
  },
});

const DASHBOARD_HTML = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CorvinOS Telemetry Dashboard</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.css" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
            color: #e6edf3;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.5em;
            background: linear-gradient(135deg, #79c0ff, #58a6ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        .stat-card {
            background: rgba(48, 54, 61, 0.8);
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }
        .stat-card h3 {
            color: #79c0ff;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }
        .stat-card .value {
            font-size: 2em;
            font-weight: bold;
            color: #58a6ff;
        }
        .map-container {
            background: rgba(48, 54, 61, 0.8);
            border: 1px solid #30363d;
            border-radius: 8px;
            margin-bottom: 40px;
            overflow: hidden;
        }
        #map {
            height: 400px;
            width: 100%;
        }
        .instances-table {
            background: rgba(48, 54, 61, 0.8);
            border: 1px solid #30363d;
            border-radius: 8px;
            overflow: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th, td {
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }
        th {
            background: rgba(33, 38, 45, 0.8);
            color: #79c0ff;
            font-weight: 600;
        }
        tr:hover { background: rgba(56, 65, 77, 0.5); }
        .loading { text-align: center; padding: 40px; }
        .spinner { display: inline-block; width: 40px; height: 40px; border: 4px solid #30363d; border-top: 4px solid #58a6ff; border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌍 CorvinOS Telemetry Dashboard</h1>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>Instances</h3>
                <div class="value" id="instanceCount">-</div>
            </div>
            <div class="stat-card">
                <h3>Total Turns</h3>
                <div class="value" id="totalTurns">-</div>
            </div>
            <div class="stat-card">
                <h3>Total Tokens</h3>
                <div class="value" id="totalTokens">-</div>
            </div>
            <div class="stat-card">
                <h3>Avg Savings</h3>
                <div class="value" id="avgSavings">-</div>
            </div>
        </div>

        <div class="map-container">
            <div id="map" class="loading"><div class="spinner"></div> Loading map...</div>
        </div>

        <div class="instances-table">
            <table>
                <thead>
                    <tr>
                        <th>Instance ID</th>
                        <th>Hostname</th>
                        <th>Location</th>
                        <th>Turns</th>
                        <th>Tokens</th>
                        <th>Savings %</th>
                    </tr>
                </thead>
                <tbody id="instancesBody">
                    <tr><td colspan="6" class="loading"><div class="spinner"></div> Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <p style="text-align: center; color: #7d8590; margin-top: 40px; font-size: 0.9em;" id="lastUpdated"></p>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <script>
        const API_URL = '/api/metrics/stats';
        let map = null;

        async function fetchStats() {
            try {
                const response = await fetch(API_URL);
                const data = await response.json();
                updateDashboard(data);
            } catch (error) {
                console.error('Error fetching stats:', error);
                setTimeout(fetchStats, 5000);
            }
        }

        function updateDashboard(data) {
            const cluster = data.cluster || {};

            document.getElementById('instanceCount').textContent = cluster.instance_count || 0;
            document.getElementById('totalTurns').textContent = (cluster.total_turns || 0).toLocaleString();
            document.getElementById('totalTokens').textContent = (cluster.total_tokens || 0).toLocaleString();
            document.getElementById('avgSavings').textContent = \`\${(cluster.avg_savings_percent || 0).toFixed(1)}%\`;

            const timestamp = new Date(data.timestamp);
            document.getElementById('lastUpdated').textContent =
                \`Last updated: \${timestamp.toLocaleTimeString()}\`;

            updateInstancesTable(cluster.instances || []);
            initMap(cluster.instances || []);
        }

        function updateInstancesTable(instances) {
            const tbody = document.getElementById('instancesBody');
            tbody.innerHTML = instances.map(inst => \`
                <tr>
                    <td><strong>\${inst.instance_id}</strong></td>
                    <td>\${inst.hostname}</td>
                    <td>\${inst.location}</td>
                    <td>\${inst.turn_count?.toLocaleString() || 0}</td>
                    <td>\${inst.total_tokens?.toLocaleString() || 0}</td>
                    <td style="color: #3fb950;">\${inst.savings_percent?.toFixed(1) || 0}%</td>
                </tr>
            \`).join('');
        }

        function initMap(instances) {
            if (map) return;

            const mapEl = document.getElementById('map');
            mapEl.style.height = '400px';
            mapEl.innerHTML = '';

            map = L.map('map').setView([20, 0], 2);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap',
                maxZoom: 19,
            }).addTo(map);

            instances.forEach(inst => {
                const [lat, lng] = inst.location.split(',').map(Number);
                if (!isNaN(lat) && !isNaN(lng)) {
                    const color = inst.savings_percent >= 25 ? '#3fb950' :
                                 inst.savings_percent >= 20 ? '#58a6ff' : '#d1d9e0';
                    L.circleMarker([lat, lng], {
                        radius: 8,
                        fillColor: color,
                        color: '#fff',
                        weight: 2,
                        opacity: 1,
                        fillOpacity: 0.7
                    }).bindPopup(\`<strong>\${inst.instance_id}</strong><br>\${inst.hostname}\`).addTo(map);
                }
            });
        }

        // Fetch stats every 5 seconds
        fetchStats();
        setInterval(fetchStats, 5000);
    </script>
</body>
</html>
`;

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // API endpoint
    if (path === '/api/metrics/stats') {
      return new Response(JSON.stringify(getMockStats()), {
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
          'Cache-Control': 'max-age=5',
        },
      });
    }

    // Dashboard
    if (path === '/stats' || path === '/') {
      return new Response(DASHBOARD_HTML, {
        headers: {
          'Content-Type': 'text/html',
          'Cache-Control': 'max-age=60',
        },
      });
    }

    // Health check
    if (path === '/health') {
      return new Response('OK', { status: 200 });
    }

    return new Response('Not Found', { status: 404 });
  },

  async scheduled(event: ScheduledEvent): Promise<void> {
    // Optional: Sync stats every 6 hours
    console.log('Syncing stats...');
  },
};
