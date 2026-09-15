/**
 * Console Layout — Updated to include YourTalent tab
 *
 * This is the main console layout that should wire YourTalent
 * as a new tab between Chat and Settings.
 */

import React, { useState } from 'react'
import YourTalent from './components/YourTalent'

export function ConsoleLayout() {
  const [activeTab, setActiveTab] = useState('chat')

  return (
    <div className="console-layout">
      {/* Tab Navigation */}
      <div className="console-tabs">
        <button
          className={`tab ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >
          💬 Chat
        </button>
        <button
          className={`tab ${activeTab === 'talent' ? 'active' : ''}`}
          onClick={() => setActiveTab('talent')}
        >
          🌟 Your Talent
        </button>
        <button
          className={`tab ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          ⚙️ Settings
        </button>
      </div>

      {/* Tab Content */}
      <div className="console-content">
        {activeTab === 'chat' && <ChatTab />}
        {activeTab === 'talent' && <YourTalent />}
        {activeTab === 'settings' && <SettingsTab />}
      </div>
    </div>
  )
}

function ChatTab() {
  return <div className="tab-content">Chat Tab Content Here</div>
}

function SettingsTab() {
  return <div className="tab-content">Settings Tab Content Here</div>
}

export default ConsoleLayout
