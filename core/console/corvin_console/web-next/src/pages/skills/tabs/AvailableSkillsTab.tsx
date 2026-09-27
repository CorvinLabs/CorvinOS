/**
 * Available Skills Tab.
 *
 * This tab used to fetch /v1/skills/available (served by no router) and
 * render marketplace ratings/review counts from it, then "install" by POSTing
 * to another absent route. The skills manager backend
 * (routes/skill_manager.py) installs from an uploaded ZIP only; browsing the
 * marketplace catalogue lives in the Marketplace panel. So this tab says so
 * instead of rendering a catalogue it cannot install from.
 */
import { Link } from 'react-router-dom';

export function AvailableSkillsTab() {
  return (
    <div className="text-center py-12 text-muted-foreground" data-testid="available-skills-unavailable">
      <p>Installing skills from a catalogue is not available on this page on this build.</p>
      <p className="text-sm mt-2">
        Browse skills in the <Link className="underline" to="/app/marketplace">Marketplace</Link>, or install a
        skill package ZIP from the "Upload" tab.
      </p>
    </div>
  );
}
