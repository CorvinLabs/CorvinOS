/**
 * Settings page with Preset Switcher and Feature Status Dashboard (Phase 5, ADR-0287/0288)
 */

import PresetSwitcher from '../components/PresetSwitcher';
import FeatureStatusDashboard from '../components/FeatureStatusDashboard';

export function Settings() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 16px' }}>
      <h1>Settings</h1>

      {/* Preset Switcher */}
      <section style={{ marginBottom: '32px' }}>
        <PresetSwitcher />
      </section>

      {/* Feature Status Dashboard */}
      <section>
        <FeatureStatusDashboard />
      </section>
    </div>
  );
}

export default Settings;
