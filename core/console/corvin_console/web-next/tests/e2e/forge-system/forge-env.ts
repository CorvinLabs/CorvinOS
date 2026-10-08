/**
 * Where the two isolated installs live — ONE definition for the Playwright config
 * and the fixtures. Overridable so two checkouts (or two reviewers) can run the
 * suite side by side: the old hard-coded homes and ports made concurrent runs
 * corrupt each other (R4-I-5).
 *
 *   FORGE_E2E_PORT_A / FORGE_E2E_PORT_B   default 8799 / 8798
 *   FORGE_E2E_HOME_A / FORGE_E2E_HOME_B   default /tmp/corvin-forge-e2e{,-b}
 *
 * Different HOSTS (127.0.0.1 vs localhost): cookies are per host, not per port.
 */
const env = process.env;
export const PORT_A = Number(env.FORGE_E2E_PORT_A ?? 8799);
export const PORT_B = Number(env.FORGE_E2E_PORT_B ?? 8798);
export const HOME_A = env.FORGE_E2E_HOME_A ?? '/tmp/corvin-forge-e2e';
export const HOME_B = env.FORGE_E2E_HOME_B ?? '/tmp/corvin-forge-e2e-b';
export const URL_A = `http://127.0.0.1:${PORT_A}`;
export const URL_B = `http://localhost:${PORT_B}`;
