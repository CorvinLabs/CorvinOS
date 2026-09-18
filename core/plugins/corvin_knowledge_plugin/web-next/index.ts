/**
 * Corvin-Knowledge Console Panel Registration
 * Wires the React graph panel into the Corvin Console
 */

export { GraphPanel } from './panels/GraphPanel';
export { GraphVisualization } from './components/GraphVisualization';
export { SettingsPanel } from './components/SettingsPanel';

/**
 * Panel metadata for Corvin Console discovery
 */
export const PANEL_CONFIG = {
  id: 'corvin-knowledge-graph',
  name: 'Knowledge Graph Explorer',
  description: 'Interactive visualization and navigation of knowledge graph',
  icon: 'database',
  route: '/console/plugins/corvin-knowledge/graph',
  component: 'GraphPanel',
  category: 'knowledge-management',
  version: '1.0.0',

  // Installation/Deinstallation hooks
  hooks: {
    onInstall: async () => {
      console.log('📦 Corvin-Knowledge plugin installed');
      // Initialize default settings
      await fetch('/v1/console/plugins/corvin-knowledge/init', {
        method: 'POST',
      });
    },
    onUninstall: async () => {
      console.log('🗑️  Corvin-Knowledge plugin uninstalled');
      // Cleanup on uninstall
      await fetch('/v1/console/plugins/corvin-knowledge/cleanup', {
        method: 'POST',
      });
    },
  },

  // API endpoints this panel requires
  api: {
    endpoints: [
      '/v1/console/plugins/corvin-knowledge/config',
      '/v1/console/plugins/corvin-knowledge/graph',
      '/v1/console/plugins/corvin-knowledge/sync',
      '/v1/console/plugins/corvin-knowledge/init',
      '/v1/console/plugins/corvin-knowledge/cleanup',
    ],
    methods: ['GET', 'POST'],
  },

  // Settings panel
  settingsPanel: {
    id: 'corvin-knowledge-settings',
    name: 'Settings',
    description: 'Configure repository and sync settings',
    icon: 'settings',
    component: 'SettingsPanel',
  },

  // Dark mode support
  features: {
    darkMode: true,
    responsive: true,
    offline: true, // Works with local repo
  },
};

// CSS Variables for dark mode (injected by console)
export const THEME_VARIABLES = {
  light: {
    '--color-background': '#ffffff',
    '--color-background-secondary': '#f5f5f5',
    '--color-border': '#e0e0e0',
    '--color-text': '#333333',
    '--color-text-secondary': '#666666',
    '--color-primary': '#2196F3',
    '--color-error-background': '#ffcdd2',
    '--color-error-text': '#c62828',
  },
  dark: {
    '--color-background': '#1e1e1e',
    '--color-background-secondary': '#2d2d2d',
    '--color-border': '#404040',
    '--color-text': '#e0e0e0',
    '--color-text-secondary': '#999999',
    '--color-primary': '#42A5F5',
    '--color-error-background': '#b71c1c',
    '--color-error-text': '#ff8a8a',
  },
};
