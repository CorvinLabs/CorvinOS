/**
 * Vibe Engineering Dashboard
 * 9D Maturity visualization with live voice integration
 *
 * Displays:
 * - 9-dimensional maturity score (hexagon radar)
 * - Live skill execution traces
 * - Voice narration of metrics
 * - Learning loop feedback
 */

import React, { useState, useEffect } from 'react'
import { BarChart, LineChart, PieChart, Activity, Volume2, Zap } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

interface Dimension {
  name: string
  value: number  // 0-10
  icon: React.ReactNode
}

interface MaturityScore {
  overall: number  // 0-10
  dimensions: Dimension[]
  timestamp: string
  voice_narration?: string
}

export const VibeDashboard: React.FC = () => {
  const [score, setScore] = useState<MaturityScore | null>(null)
  const [isNarrating, setIsNarrating] = useState(false)
  const [trends, setTrends] = useState<number[]>([])

  // Load maturity score from API
  useEffect(() => {
    const loadScore = async () => {
      try {
        const response = await fetch('/api/v1/vibe/maturity-score')
        if (response.ok) {
          const data = await response.json()
          setScore(data)
          setTrends([...trends.slice(-8), data.overall])
        }
      } catch (error) {
        console.error('Failed to load maturity score:', error)
      }
    }

    loadScore()
    const interval = setInterval(loadScore, 30000) // Refresh every 30s
    return () => clearInterval(interval)
  }, [])

  // Trigger voice narration
  const handleVoiceNarration = async () => {
    if (!score) return
    setIsNarrating(true)

    try {
      const narration = `CorvinOS maturity score is ${score.overall} out of ten.
      Architecture readiness: ${score.dimensions[0].value}.
      Plugin ecosystem: ${score.dimensions[1].value}.
      Learning capabilities: ${score.dimensions[2].value}.`

      // Generate TTS using edge-tts API
      const response = await fetch('/api/v1/voice/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: narration, voice: 'en-US-AvaMultilingualNeural' })
      })

      if (response.ok) {
        const { audio_url } = await response.json()
        const audio = new Audio(audio_url)
        audio.play()
      }
    } catch (error) {
      console.error('TTS failed:', error)
    } finally {
      setIsNarrating(false)
    }
  }

  if (!score) {
    return (
      <Card className="p-8 text-center">
        <Activity className="mx-auto mb-4 animate-spin" />
        <p>Loading CorvinOS maturity metrics...</p>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header with overall score */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-800">CorvinOS Maturity</h2>
            <p className="text-gray-600">9-Dimensional System Health</p>
          </div>
          <div className="text-center">
            <div className="text-6xl font-bold text-blue-600">{score.overall.toFixed(1)}</div>
            <p className="text-gray-600">/ 10</p>
          </div>
        </div>

        {/* Voice narration button */}
        <button
          onClick={handleVoiceNarration}
          disabled={isNarrating}
          className="mt-4 flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          <Volume2 size={20} />
          {isNarrating ? 'Narrating...' : 'Narrate Metrics'}
        </button>
      </div>

      {/* 9 Dimension grid */}
      <div className="grid grid-cols-3 gap-4">
        {score.dimensions.map((dim) => (
          <Card key={dim.name} className="p-4 hover:shadow-lg transition-shadow">
            <div className="flex items-start justify-between mb-3">
              <h3 className="font-semibold text-gray-800">{dim.name}</h3>
              <div className="text-blue-600">{dim.icon}</div>
            </div>

            {/* Score bar */}
            <div className="mb-2">
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all"
                  style={{ width: `${dim.value * 10}%` }}
                />
              </div>
            </div>

            {/* Score value and trend */}
            <div className="flex items-end justify-between">
              <span className="text-2xl font-bold text-gray-800">{dim.value.toFixed(1)}</span>
              <Badge
                variant={dim.value >= 7 ? 'default' : dim.value >= 5 ? 'secondary' : 'destructive'}
              >
                {dim.value >= 7 ? '↑ Strong' : dim.value >= 5 ? '→ Fair' : '↓ Needs work'}
              </Badge>
            </div>
          </Card>
        ))}
      </div>

      {/* Trend chart */}
      <Card className="p-6">
        <h3 className="font-semibold text-gray-800 mb-4">Maturity Trend (Last 30 days)</h3>
        <div className="flex items-end gap-1 h-32">
          {trends.map((value, i) => (
            <div
              key={i}
              className="flex-1 bg-blue-400 rounded-t hover:bg-blue-600 transition-colors"
              style={{ height: `${(value / 10) * 100}%` }}
              title={`${value.toFixed(1)}`}
            />
          ))}
        </div>
      </Card>

      {/* Learning feedback */}
      <Card className="p-6 bg-green-50">
        <div className="flex items-start gap-4">
          <Zap className="text-green-600 flex-shrink-0" size={24} />
          <div>
            <h3 className="font-semibold text-gray-800 mb-2">Learning in Progress</h3>
            <p className="text-gray-600">
              CorvinOS is learning from user feedback and optimizing Skills configuration.
              Check back in 24 hours for next iteration.
            </p>
          </div>
        </div>
      </Card>

      {/* Timestamp */}
      <div className="text-sm text-gray-500 text-right">
        Last updated: {new Date(score.timestamp).toLocaleTimeString()}
      </div>
    </div>
  )
}
