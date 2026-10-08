import path from 'path';
import { fileURLToPath } from 'url';

/** <checkout>/ — seven levels above this file (web-next/tests/e2e/forge-system → repo root). */
export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../../..');
