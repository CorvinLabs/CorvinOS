import React, { useEffect, useState } from 'react'
import { Activity, Globe, Zap, TrendingUp, AlertCircle } from 'lucide-react'

interface Instance {
  instance_id: string
  region: string
  latitude: number
  longitude: number
  uptime_hours: number
  total_requests: number
  error_count: number
  error_rate_pct: number
  cost_usd: number
  avg_latency_ms: number
  learning_confidence: number
  last_heartbeat_iso: string
}

interface GlobalStats {
  summary: {
    total_instances: number
    total_requests: number
    total_errors: number
    error_rate_pct: number
    total_cost_usd: number
    avg_confidence: number
    timestamp: string
  }
  instances: Instance[]
}

export default function StatsLivePage() {
  const [stats, setStats] = useState<GlobalStats | null>(null)
  const [modelBreakdown, setModelBreakdown] = useState<any>(null)
  const [health, setHealth] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<string>("")

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const [statsRes, modelsRes, healthRes] = await Promise.all([
          fetch('/v1/stats/live'),
          fetch('/v1/stats/live/models'),
          fetch('/v1/stats/live/health')
        ])
        
        const [statsData, modelsData, healthData] = await Promise.all([
          statsRes.json(),
          modelsRes.json(),
          healthRes.json()
        ])
        
        setStats(statsData)
        setModelBreakdown(modelsData)
        setHealth(healthData)
        setLastUpdate(new Date().toLocaleTimeString())
        setLoading(false)
      } catch (error) {
        console.error('Error fetching stats:', error)
        setLoading(false)
      }
    }

    fetchStats()
    const interval = setInterval(fetchStats, 3000) // Update every 3 seconds
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return <div className="p-8 text-center">Loading live stats...</div>
  }

  if (!stats) {
    return <div className="p-8 text-center text-red-500">Error loading stats</div>
  }

  const { summary, instances } = stats

  return (
    <div className="p-8 bg-slate-900 min-h-screen text-white">
      <h1 className="text-4xl font-bold mb-2">CorvinOS Live Stats</h1>
      <p className="text-gray-400 mb-6">Last updated: {lastUpdate}</p>

      {/* Global Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard
          icon={<Zap className="w-6 h-6" />}
          label="Total Requests"
          value={summary.total_requests.toLocaleString()}
          color="blue"
        />
        <StatCard
          icon={<AlertCircle className="w-6 h-6" />}
          label="Error Rate"
          value={`${summary.error_rate_pct.toFixed(2)}%`}
          color={summary.error_rate_pct > 0.1 ? "red" : "green"}
        />
        <StatCard
          icon={<TrendingUp className="w-6 h-6" />}
          label="Total Cost"
          value={`$${summary.total_cost_usd.toFixed(0)}`}
          color="amber"
        />
        <StatCard
          icon={<Activity className="w-6 h-6" />}
          label="Avg Confidence"
          value={`${(summary.avg_confidence * 100).toFixed(1)}%`}
          color="emerald"
        />
      </div>

      {/* Model Breakdown */}
      {modelBreakdown && (
        <div className="bg-slate-800 p-6 rounded-lg mb-8">
          <h2 className="text-xl font-bold mb-4">Model Usage Distribution</h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-blue-400">
                {modelBreakdown.distribution_pct.haiku.toFixed(1)}%
              </div>
              <div className="text-gray-400">Haiku</div>
              <div className="text-sm text-gray-500">
                {modelBreakdown.model_usage.haiku.toLocaleString()} calls
              </div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-purple-400">
                {modelBreakdown.distribution_pct.sonnet.toFixed(1)}%
              </div>
              <div className="text-gray-400">Sonnet</div>
              <div className="text-sm text-gray-500">
                {modelBreakdown.model_usage.sonnet.toLocaleString()} calls
              </div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-red-400">
                {modelBreakdown.distribution_pct.opus.toFixed(1)}%
              </div>
              <div className="text-gray-400">Opus</div>
              <div className="text-sm text-gray-500">
                {modelBreakdown.model_usage.opus.toLocaleString()} calls
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Health Status */}
      {health && (
        <div className="bg-slate-800 p-6 rounded-lg mb-8">
          <h2 className="text-xl font-bold mb-4">Cluster Health</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-2xl font-bold">
                <span className={health.healthy_instances > 0 ? "text-green-400" : "text-gray-500"}>
                  {health.healthy_instances}
                </span>
              </div>
              <div className="text-gray-400">Healthy Instances</div>
            </div>
            <div>
              <div className="text-2xl font-bold">
                <span className={health.critical_instances > 0 ? "text-red-400" : "text-gray-500"}>
                  {health.critical_instances}
                </span>
              </div>
              <div className="text-gray-400">Critical</div>
            </div>
            <div>
              <div className="text-lg font-bold text-cyan-400">
                {health.avg_latency_ms.toFixed(0)}ms
              </div>
              <div className="text-gray-400">Avg Latency</div>
            </div>
            <div>
              <div className="text-lg font-bold text-yellow-400">
                {health.avg_uptime_hours.toFixed(0)}h
              </div>
              <div className="text-gray-400">Avg Uptime</div>
            </div>
          </div>
        </div>
      )}

      {/* Instance List */}
      <div className="bg-slate-800 p-6 rounded-lg">
        <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
          <Globe className="w-5 h-5" /> Instances
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-700">
              <tr className="text-gray-400">
                <th className="text-left py-2">Instance</th>
                <th className="text-left py-2">Region</th>
                <th className="text-right py-2">Requests</th>
                <th className="text-right py-2">Error Rate</th>
                <th className="text-right py-2">Cost</th>
                <th className="text-right py-2">Latency</th>
                <th className="text-right py-2">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {instances.map((instance) => (
                <tr key={instance.instance_id} className="border-b border-slate-700 hover:bg-slate-700/50">
                  <td className="py-3 font-mono text-xs">{instance.instance_id.split('-').pop()}</td>
                  <td className="py-3">{instance.region}</td>
                  <td className="py-3 text-right">{instance.total_requests.toLocaleString()}</td>
                  <td className="py-3 text-right">
                    <span className={instance.error_rate_pct > 0.1 ? "text-red-400" : "text-green-400"}>
                      {instance.error_rate_pct.toFixed(2)}%
                    </span>
                  </td>
                  <td className="py-3 text-right">${instance.cost_usd.toFixed(0)}</td>
                  <td className="py-3 text-right">{instance.avg_latency_ms.toFixed(0)}ms</td>
                  <td className="py-3 text-right text-cyan-400">{(instance.learning_confidence * 100).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function StatCard({ icon, label, value, color }: any) {
  const colorClasses = {
    blue: "text-blue-400 border-blue-500",
    red: "text-red-400 border-red-500",
    green: "text-green-400 border-green-500",
    amber: "text-amber-400 border-amber-500",
    emerald: "text-emerald-400 border-emerald-500"
  }
  
  return (
    <div className={`border-l-4 ${colorClasses[color as keyof typeof colorClasses]} bg-slate-800 p-4 rounded`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-400 text-sm">{label}</span>
        {icon}
      </div>
      <div className="text-2xl font-bold">{value}</div>
    </div>
  )
}
