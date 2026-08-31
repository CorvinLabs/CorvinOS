import React from 'react';

export const LoginPage = () => (
  <div>
    <h1>Login</h1>
    <form>
      <label>
        Email
        <input type="email" name="email" />
      </label>
      <label>
        Password
        <input type="password" name="password" />
      </label>
      <button type="submit">Sign In</button>
    </form>
  </div>
);

export const DashboardPage = () => (
  <div>
    <h1>Corvin overview</h1>
    <div>Engines online: 2</div>
    <div>Channels: Discord, Telegram, Slack</div>
    <div>Audit events today: 142</div>
    <div>Uptime: 99.8%</div>
    <div>Last sync: 2026-06-02T10:30:00Z</div>
    <div>Personas: Assistant, Coder, Researcher</div>
  </div>
);

export const ComputePage = () => (
  <div>
    <h1>Compute Jobs</h1>
    <button>New Job</button>
    <div>
      <div>Job 1: SELECT COUNT(*) - Completed</div>
      <div>Job 2: SELECT * FROM users - Running (45%)</div>
    </div>
  </div>
);

export const ForgePage = () => (
  <div>
    <h1>Forge Tools</h1>
    <button>Create Tool</button>
    <input type="text" name="search" placeholder="Search tools" />
    <div>
      <div>code.example_tool: Example tool for testing</div>
      <div>code.data_processor: Process data streams</div>
    </div>
  </div>
);

export const SettingsPage = () => (
  <div>
    <h1>Settings</h1>
    <div>
      <label>
        Theme:
        <select name="theme">
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </label>
    </div>
    <div>
      <label>
        <input type="checkbox" name="notifications" />
        Enable Notifications
      </label>
    </div>
    <div>
      <label>
        Timeout (ms):
        <input type="number" name="timeout" min="0" />
      </label>
    </div>
    <button>Save</button>
    <button>Reset to Defaults</button>
  </div>
);

export const ChatPage = () => (
  <div>
    <h1>Chat</h1>
    <div role="main">
      <div>
        <div>Assistant: Hello! How can I help you?</div>
        <div>User: What is CorvinOS?</div>
        <div>Assistant: CorvinOS is an AI-powered framework...</div>
      </div>
      <form>
        <textarea placeholder="Type your message..."></textarea>
        <button type="submit">Send</button>
      </form>
    </div>
  </div>
);

export const WorkflowsPage = () => (
  <div>
    <h1>Workflows</h1>
    <button>Create Workflow</button>
    <input type="text" placeholder="Search workflows" />
    <div>
      <div role="row">
        <span>workflow-1</span>
        <span>discovering</span>
        <button>Edit</button>
      </div>
      <div role="row">
        <span>workflow-2</span>
        <span>ready</span>
        <button>Execute</button>
      </div>
    </div>
  </div>
);

export const CompliancePage = () => (
  <div>
    <h1>Compliance & Audit</h1>
    <div data-testid="audit-chain-section">
      <h2>Audit Chain Status</h2>
      <div data-testid="audit-chain-status">Status: Verified</div>
      <div>Total events: 342</div>
      <div data-testid="audit-chain-verified">Last verified: 2026-06-02T10:15:00Z</div>
    </div>
    <div data-testid="gdpr-section">
      <h2>GDPR</h2>
      <div>Data retention: 90 days</div>
      <div>Erasure requests: 0 pending</div>
    </div>
    <div data-testid="eu-ai-act-section">
      <h2>EU AI Act</h2>
      <div data-testid="bot-disclosure">Bot disclosure: Active</div>
      <div>Consent gate: Enabled</div>
    </div>
    <button>Download Audit Log</button>
  </div>
);

export const VoicePage = () => (
  <div>
    <h1>Voice Settings</h1>
    <div data-testid="stt-provider-section">
      <label>
        STT Provider:
        <select name="stt">
          <option value="openai">OpenAI Whisper</option>
          <option value="local">Local Whisper</option>
        </select>
      </label>
    </div>
    <div data-testid="tts-voice-section">
      <label>
        TTS Voice:
        <select name="voice" data-testid="voice-select">
          <option value="nova">Nova (English)</option>
          <option value="shimmer">Shimmer (English)</option>
          <option value="alloy">Alloy (English)</option>
        </select>
      </label>
    </div>
    <button>Test Voice</button>
  </div>
);

export const EnginesPage = () => (
  <div>
    <h1>AI Engines</h1>
    <div>
      <div role="option" data-testid="engine-claude">
        <span data-testid="engine-claude-name">Claude Code (Local)</span>
        <span data-testid="engine-claude-status">Status: Active</span>
        <button>Select</button>
      </div>
      <div role="option" data-testid="engine-hermes">
        <span data-testid="engine-hermes-name">Hermes (Local Ollama)</span>
        <span data-testid="engine-hermes-status">Status: Available</span>
        <button>Select</button>
      </div>
      <div role="option" data-testid="engine-opencode">
        <span data-testid="engine-opencode-name">OpenCodeEngine</span>
        <span data-testid="engine-opencode-status">Status: Available</span>
        <button>Select</button>
      </div>
    </div>
  </div>
);

export const BridgesPage = () => (
  <div>
    <h1>Bridges & Channels</h1>
    <div>
      <div role="region" data-testid="bridge-discord">
        <h2>Discord</h2>
        <div data-testid="discord-status">Connected: Yes</div>
        <div>Channels: 3</div>
        <button>Configure</button>
      </div>
      <div role="region" data-testid="bridge-telegram">
        <h2>Telegram</h2>
        <div data-testid="telegram-status">Connected: Yes</div>
        <div>Bot ID: @corvin_bot</div>
        <button>Configure</button>
      </div>
      <div role="region" data-testid="bridge-slack">
        <h2>Slack</h2>
        <div data-testid="slack-status">Connected: No</div>
        <button>Connect</button>
      </div>
    </div>
  </div>
);
