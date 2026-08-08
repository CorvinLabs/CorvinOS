/**
 * Your Talent — Console Component
 *
 * Live "Your Talent" tab showing:
 * - Talent Score (0–10)
 * - Context Ranking
 * - Learning Timeline
 * - Training Actions
 */

import React, { useEffect, useState } from 'react'
import './YourTalent.css'

const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5000'

export function YourTalent() {
  const [talentData, setTalentData] = useState(null)
  const [ranking, setRanking] = useState([])
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedContext, setSelectedContext] = useState(null)

  // Fetch talent data every 30 seconds
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true)
        const [scoreRes, rankRes, eventsRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/talent/score?days=7`),
          fetch(`${API_BASE}/api/v1/talent/ranking?days=7`),
          fetch(`${API_BASE}/api/v1/talent/events?days=7`),
        ])

        if (!scoreRes.ok || !rankRes.ok || !eventsRes.ok) {
          throw new Error('API error')
        }

        const scoreData = await scoreRes.json()
        const rankData = await rankRes.json()
        const eventsData = await eventsRes.json()

        setTalentData(scoreData)
        setRanking(rankData.ranking || [])
        setEvents(eventsData.events || [])
        setError(null)
      } catch (err) {
        console.error('Failed to fetch talent data:', err)
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  if (loading && !talentData) {
    return (
      <div className="talent-container">
        <div className="talent-loading">🌟 Laden der Talent-Metriken...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="talent-container">
        <div className="talent-error">
          ⚠️ Fehler beim Laden: {error}
        </div>
      </div>
    )
  }

  const score = talentData?.talent_score || 0
  const trend = talentData?.trend || 0

  return (
    <div className="talent-container">
      {/* Header */}
      <div className="talent-header">
        <h2>🌟 Dein Talent</h2>
        <p>Wie lernt Dein System wirklich?</p>
      </div>

      {/* Talent Score Card */}
      <div className="talent-score-card">
        <div className="score-display">
          <div className="score-number">{score.toFixed(1)}</div>
          <div className="score-label">/10</div>
        </div>
        <div className="score-info">
          <h3>Dein System wächst</h3>
          <div className="score-stats">
            <div className="stat">
              <span className="label">Wachstum diese Woche</span>
              <span className="value" style={{ color: trend > 0 ? '#00AA44' : '#FF8800' }}>
                {trend > 0 ? '↗' : '↘'} {Math.abs(trend).toFixed(1)} Punkte
              </span>
            </div>
            <div className="stat">
              <span className="label">Status</span>
              <span className="value">
                {score >= 8 ? '🚀 Schnell lernend' : score >= 6 ? '📈 Gutes Tempo' : '⏳ Aufbauphase'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Ranking Section */}
      <div className="ranking-section">
        <h3>🏆 Deine Top-Kontexte</h3>
        <div className="ranking-grid">
          {ranking.slice(0, 5).map((ctx) => (
            <ContextCard
              key={ctx.id}
              context={ctx}
              onSelect={() => setSelectedContext(ctx)}
            />
          ))}
        </div>
      </div>

      {/* Events Section */}
      <div className="events-section">
        <h3>📰 Letzte Learning Events</h3>
        <div className="events-list">
          {events.slice(0, 5).map((event, idx) => (
            <div key={idx} className="event-item">
              <div className="event-time">
                {new Date(event.timestamp).toLocaleTimeString('de-DE')}
              </div>
              <div className="event-content">
                <div className="event-title">{event.title}</div>
                <div className="event-desc">{event.description}</div>
                {event.badge && <span className="event-badge">{event.badge}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="actions-section">
        <h3>🎯 Was willst Du tun?</h3>
        <div className="action-buttons">
          <button className="action-btn">
            💬 Feedback Geben
            <small>Zeig mir: War das nützlich?</small>
          </button>
          <button className="action-btn">
            🔗 Context Pairing
            <small>Nutze 2 Kontexte zusammen</small>
          </button>
          <button className="action-btn">
            🧪 Isolation Test
            <small>Deaktiviere einen Kontext</small>
          </button>
          <button className="action-btn">
            📚 Retraining
            <small>Zeige Beispiele wo das gut läuft</small>
          </button>
        </div>
      </div>

      {/* Deep Dive Modal */}
      {selectedContext && (
        <DeepDiveModal context={selectedContext} onClose={() => setSelectedContext(null)} />
      )}
    </div>
  )
}

function ContextCard({ context, onSelect }) {
  const getMedalAndStatus = (ctx) => {
    if (ctx.rank === 1) return { medal: '🏆', status: 'MENTOR' }
    if (ctx.rank === 2) return { medal: '🥈', status: 'STRONG' }
    if (ctx.rank === 3) return { medal: '🥉', status: 'SOLID' }
    if (ctx.accuracy < 0.75) return { medal: '⚠️', status: 'NEEDS_TRAINING' }
    if (ctx.accuracy < 0.70) return { medal: '🚨', status: 'STRUGGLING' }
    return { medal: '📌', status: 'ACTIVE' }
  }

  const { medal, status } = getMedalAndStatus(context)
  const accColor = context.accuracy >= 0.85 ? '#00AA44' : context.accuracy >= 0.75 ? '#FF8800' : '#CC0000'

  return (
    <div className="context-card" style={{ borderLeftColor: accColor }} onClick={onSelect}>
      <div className="context-header">
        <span className="medal">{medal}</span>
        <span className="name">{context.id}</span>
        <span className="status-badge" style={{ background: `${accColor}20`, color: accColor }}>
          {status}
        </span>
      </div>

      <div className="context-bar">
        <div
          className="bar-fill"
          style={{
            width: `${context.accuracy * 100}%`,
            background: `linear-gradient(90deg, ${accColor}, ${accColor}dd)`,
          }}
        ></div>
      </div>

      <div className="context-stats">
        <div className="stat-item">
          <span className="label">Genauigkeit</span>
          <span className="value">{(context.accuracy * 100).toFixed(0)}%</span>
        </div>
        <div className="stat-item">
          <span className="label">Verwendet</span>
          <span className="value">{context.usage}×</span>
        </div>
        <div className="stat-item">
          <span className="label">Feedback</span>
          <span className="value">{context.feedback_pct.toFixed(0)}%</span>
        </div>
      </div>

      <button className="deep-dive-btn">Deep Dive →</button>
    </div>
  )
}

function DeepDiveModal({ context, onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>

        <h2>{context.id}</h2>
        <p className="modal-subtitle">Deep Dive Analysis</p>

        <div className="modal-stats">
          <div className="stat-box">
            <span className="stat-label">Aktuelle Genauigkeit</span>
            <span className="stat-value">{(context.accuracy * 100).toFixed(1)}%</span>
          </div>
          <div className="stat-box">
            <span className="stat-label">Mal verwendet</span>
            <span className="stat-value">{context.usage}</span>
          </div>
          <div className="stat-box">
            <span className="stat-label">Feedback Positiv</span>
            <span className="stat-value">{context.feedback_pct.toFixed(1)}%</span>
          </div>
          <div className="stat-box">
            <span className="stat-label">Status</span>
            <span className="stat-value">{context.status}</span>
          </div>
        </div>

        <div className="modal-section">
          <h3>Empfehlung</h3>
          <p>
            {context.accuracy >= 0.85
              ? `Dieser Kontext ist ein MVP! Nutze ihn zuerst bei ähnlichen Aufgaben.`
              : context.accuracy >= 0.70
                ? `Guter Kontext mit Potenzial. Regelmäßig nutzen um zu verbessern.`
                : `Dieser Kontext braucht Training. Geben Sie Feedback um ihm zu helfen.`}
          </p>
        </div>

        <div className="modal-actions">
          <button className="action-btn-modal">💬 Feedback Geben</button>
          <button className="action-btn-modal">📚 Retraining</button>
        </div>
      </div>
    </div>
  )
}

export default YourTalent
