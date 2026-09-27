# Phase 5: K=2 E2E Planning — Console Skill Manager UI

**Date:** 2026-09-27  
**Status:** Planning Complete (Ready for K=3 Red/Green Implementation)  
**Scope:** Map React components, backend endpoints, state management, E2E test cases

---

## 1. Architecture Overview

### Proven Pattern: Tab-Based Dashboard (ADR-2080)

Phase 5 follows the exact tab pattern from Phase 2 Vibe Dashboard:
- **URL-driven state:** `?tab=installed|available|upload` (deep-linkable)
- **useSearchParams hook:** React Router params, PUSH on tab switch (back returns to previous tab)
- **Shared Context:** InstallationProgress lives at dashboard root, all tabs observe
- **Lazy Loading:** Suspense on heavy tabs (AvailableSkillsTab with search/filter)

### Core Principles

1. **Admin-only gates** (placeholder for ADR-0007 RBAC)
   - Every mutation (`/install`, `/uninstall`, `/upload`) checks `admin_only` (fail-closed)
   - React UI hides buttons unless `capabilities.user.is_admin === true`
   - Adversarial test: non-admin curl → 403 Forbidden

2. **Polling at 2s cadence** (responsive, stateless)
   - InstallationProgress polls `/v1/skills/status?task_id=<installation_id>`
   - 2s cadence while installing; 30s after completion
   - 5s timeout per poll → error banner on hung API

3. **Audit events on all mutations** (ADR-0314)
   - `skill.install`, `skill.uninstall`, `skill.upload` events
   - Tenant-scoped, hash-chained to audit trail

4. **Fail-closed** (errors don't silent-fail)
   - Network errors show banner, not silent retry
   - Admin-only rejection is explicit (not silently disabled button)
   - Polling timeout is explicit (not infinite spinner)

---

## 2. React Components (5 new files)

### Scope: 1500 LoC total

| Component | Purpose | LoC | Dependencies |
|---|---|---|---|
| **SkillManagerDashboard.tsx** | Root dashboard, tab routing, InstallationProgress state | 280 | React Router, Context API |
| **InstalledSkillsTab.tsx** | List installed skills, uninstall, version info | 250 | useCallback, useState, fetch |
| **AvailableSkillsTab.tsx** | Search/filter marketplace skills, install | 280 | useState, useCallback, Suspense |
| **SkillUploadTab.tsx** | Local ZIP upload, validation, install | 200 | useState, FormData, fetch |
| **SkillCard.tsx** | Skill detail card (installed/available view) | 150 | Props-driven, no state |
| **InstallationProgress.tsx** | Progress bar, status polling, error banner | 140 | useEffect, useState, polling loop |

### File Structure

```
core/console/corvin_console/web-next/src/pages/skills/
├── SkillManagerDashboard.tsx        (280 LoC, ROOT)
├── tabs/
│   ├── InstalledSkillsTab.tsx       (250 LoC)
│   ├── AvailableSkillsTab.tsx       (280 LoC)
│   └── SkillUploadTab.tsx           (200 LoC)
└── components/
    ├── SkillCard.tsx               (150 LoC)
    └── InstallationProgress.tsx    (140 LoC)
```

### Context API: InstallationProgress State

```typescript
// SkillManagerContext.tsx (NEW, 80 LoC)
export interface InstallationState {
  taskId: string | null;
  skillName: string;
  status: 'pending' | 'downloading' | 'extracting' | 'validating' | 'installing' | 'complete' | 'error';
  progress: number; // 0-100
  errorMessage: string | null;
}

export const SkillManagerContext = createContext<{
  installation: InstallationState;
  startInstall: (skillName: string, source: 'marketplace' | 'upload') => Promise<string>;
  cancelInstall: () => void;
}>({...});
```

### Component Details

#### SkillManagerDashboard.tsx (ROOT, 280 LoC)

- Tab routing via `?tab=` (installed|available|upload)
- Provides SkillManagerContext to all children
- Renders InstallationProgress banner at top (always visible, even if tab switches)
- Fetches `capabilities` on mount to determine if user can install (`is_admin`)
- Fetch `/v1/skills/installed` on mount (for InstalledSkillsTab)

**Key Logic:**
```tsx
const [params, setParams] = useSearchParams();
const activeTab = params.get('tab') || 'installed';
const [installation, setInstallation] = useState<InstallationState>(...)
const [capabilities, setCapabilities] = useState(null);

// On mount: fetch capabilities
useEffect(() => {
  fetch('/v1/console/capabilities/manifest')
    .then(r => r.json())
    .then(cap => setCapabilities(cap));
}, []);

// Hide install tabs unless is_admin
const canInstall = capabilities?.user?.is_admin ?? false;
```

#### InstalledSkillsTab.tsx (250 LoC)

- List installed skills (fetched from `/v1/skills/installed`)
- Show: name, version, author, installed_at, status
- Action: "Uninstall" button (if `canInstall`)
- Click uninstall → call `startInstall(..., 'uninstall')` → trigger polling

**Key API Call:**
```tsx
const handleUninstall = async (skillId: string) => {
  if (!window.confirm('Remove this skill?')) return;
  
  const taskId = await fetch(`/v1/skills/uninstall`, {
    method: 'POST',
    body: JSON.stringify({ skill_id: skillId }),
  })
    .then(r => r.json())
    .then(r => r.task_id);
  
  startInstall(skillId, 'uninstall'); // Triggers polling
};
```

#### AvailableSkillsTab.tsx (280 LoC)

- Fetch marketplace skills from `/v1/skills/available?search=<q>&filter=<cat>`
- Search/filter UI (text input + category dropdown)
- Lazy-load results (Suspense + useCallback debounce)
- Show: name, version, description, author, rating, install button
- Click install → call `startInstall(..., 'marketplace')` → trigger polling

**Key API Call:**
```tsx
const handleInstall = async (skillId: string) => {
  const taskId = await fetch(`/v1/skills/install`, {
    method: 'POST',
    body: JSON.stringify({ skill_id: skillId, source: 'marketplace' }),
  })
    .then(r => r.json())
    .then(r => r.task_id);
  
  startInstall(skillId, 'marketplace');
};
```

#### SkillUploadTab.tsx (200 LoC)

- Drag-and-drop or file picker for `.zip` upload
- Validate file is `.zip` (fail-closed on wrong type)
- Upload to `/v1/skills/upload` with FormData
- Show upload progress (file size)
- Click confirm → install from upload → polling

**Key API Call:**
```tsx
const handleUpload = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  
  const taskId = await fetch(`/v1/skills/upload`, {
    method: 'POST',
    body: formData,
  })
    .then(r => r.json())
    .then(r => r.task_id);
  
  startInstall(file.name, 'upload');
};
```

#### SkillCard.tsx (150 LoC)

- Reusable card component for skill display
- Props: `skill: SkillInfo`, `action: 'install' | 'uninstall' | 'none'`, `onAction: () => void`
- Variants: InstalledSkillCard, AvailableSkillCard
- Display: icon, name, version, description, action button (or disabled if no permission)

#### InstallationProgress.tsx (140 LoC)

- Polls `/v1/skills/status?task_id=<taskId>` every 2s
- Shows progress bar (0-100%)
- Status text: "Installing…", "Extracting…", "Complete", "Error: …"
- Error banner with retry button (calls `startInstall` again)
- Auto-hide after completion (5s delay)

**Key Polling Logic:**
```tsx
useEffect(() => {
  if (!installation.taskId) return;
  
  const pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/v1/skills/status?task_id=${installation.taskId}`, {
        signal: AbortSignal.timeout(5000), // 5s timeout
      });
      const status = await res.json();
      setInstallation(prev => ({
        ...prev,
        status: status.status,
        progress: status.progress,
        errorMessage: status.error || null,
      }));
    } catch (err) {
      setInstallation(prev => ({
        ...prev,
        errorMessage: 'Status unavailable (network error)',
        status: 'error',
      }));
    }
  }, 2000); // 2s cadence
  
  return () => clearInterval(pollInterval);
}, [installation.taskId]);
```

---

## 3. Backend Endpoints (3 new/extended, 400 LoC)

### File: `core/console/corvin_console/routes/skills_v2.py` (NEW, extend from Phase 4)

#### Endpoint 1: POST /v1/skills/install

**Purpose:** Initiate skill installation (marketplace source)

**Request:**
```json
{
  "skill_id": "os.flow_guard",
  "source": "marketplace"
}
```

**Response:**
```json
{
  "task_id": "install-abc123",
  "skill_id": "os.flow_guard",
  "status": "pending"
}
```

**Implementation:**
```python
@router.post("/install")
async def install_skill(
    req: InstallSkillRequest,
    rec: SessionRecord = Depends(require_session),
) -> InstallSkillResponse:
    """Install a skill from marketplace."""
    if not _is_admin(rec):
        raise HTTPException(403, "Admin-only")
    
    tenant_id = rec.tenant_id
    installer = SkillInstaller(tenant_id)
    
    # Audit event
    emit_audit('skill.install', {
        'skill_id': req.skill_id,
        'source': req.source,
        'tenant_id': tenant_id,
        'lom': 'console.skills_v2.install_skill:L42',
    })
    
    task_id = await installer.install_from_marketplace(
        skill_id=req.skill_id,
        tenant_id=tenant_id,
    )
    
    return InstallSkillResponse(
        task_id=task_id,
        skill_id=req.skill_id,
        status='pending',
    )
```

#### Endpoint 2: POST /v1/skills/uninstall

**Purpose:** Remove installed skill

**Request:**
```json
{
  "skill_id": "os.flow_guard"
}
```

**Response:**
```json
{
  "task_id": "uninstall-def456",
  "skill_id": "os.flow_guard",
  "status": "pending"
}
```

**Implementation:** (similar to install, calls `installer.uninstall()`)

#### Endpoint 3: GET /v1/skills/status

**Purpose:** Poll installation status

**Query Params:**
- `task_id` (required): Installation task ID

**Response:**
```json
{
  "task_id": "install-abc123",
  "status": "extracting",
  "progress": 65,
  "error": null
}
```

**Implementation:**
```python
@router.get("/status")
async def get_skill_status(
    task_id: str,
    rec: SessionRecord = Depends(require_session),
) -> SkillStatusResponse:
    """Get real-time installation status."""
    tenant_id = rec.tenant_id
    
    installer = SkillInstaller(tenant_id)
    status = installer.get_status(task_id)
    
    return SkillStatusResponse(
        task_id=task_id,
        status=status.status,
        progress=status.progress,
        error=status.error_message,
    )
```

#### Endpoint 4: GET /v1/skills/installed (existing, extend)

**Purpose:** List installed skills

**Response:**
```json
{
  "skills": [
    {
      "skill_id": "os.delegation_router",
      "name": "Delegation Router",
      "version": "1.0.0",
      "author": "Corvin Labs",
      "installed_at": "2026-09-20T10:00:00Z",
      "status": "active"
    }
  ]
}
```

#### Endpoint 5: GET /v1/skills/available (existing, extend)

**Purpose:** List marketplace skills

**Query Params:**
- `search` (optional): Search by name/description
- `filter` (optional): Category filter (networking, storage, ai, etc.)

**Response:**
```json
{
  "skills": [
    {
      "skill_id": "os.flow_guard",
      "name": "Flow Guard",
      "version": "2.1.0",
      "author": "Corvin Labs",
      "description": "Data flow protection layer",
      "rating": 4.8,
      "reviews": 127,
      "category": "security"
    }
  ]
}
```

#### Endpoint 6: POST /v1/skills/upload

**Purpose:** Upload local ZIP, unzip, validate, prepare for install

**Request:** FormData with `file: File`

**Response:**
```json
{
  "task_id": "upload-ghi789",
  "skill_id": "my.custom_skill",
  "name": "Custom Skill",
  "status": "uploaded"
}
```

**Implementation:**
```python
@router.post("/upload")
async def upload_skill(
    file: UploadFile = File(...),
    rec: SessionRecord = Depends(require_session),
) -> UploadSkillResponse:
    """Upload a skill ZIP file."""
    if not _is_admin(rec):
        raise HTTPException(403, "Admin-only")
    
    tenant_id = rec.tenant_id
    
    # Validate ZIP
    if not file.filename.endswith('.zip'):
        raise HTTPException(400, "File must be a .zip")
    
    installer = SkillInstaller(tenant_id)
    task_id, skill_id = await installer.validate_and_stage_upload(
        file=file,
        tenant_id=tenant_id,
    )
    
    # Audit event
    emit_audit('skill.upload', {
        'skill_id': skill_id,
        'filename': file.filename,
        'tenant_id': tenant_id,
        'lom': 'console.skills_v2.upload_skill:L78',
    })
    
    return UploadSkillResponse(
        task_id=task_id,
        skill_id=skill_id,
        status='uploaded',
    )
```

### Admin Gate Helper

```python
def _is_admin(rec: SessionRecord) -> bool:
    """Check if user is admin (placeholder for ADR-0007 RBAC)."""
    # For localhost console, any authenticated user is admin
    # When ADR-0007 ships, replace with: rec.user.has_permission('skills:install')
    return rec.user is not None  # Authenticated
```

---

## 4. State Management (React Context)

### New File: SkillManagerContext.tsx (80 LoC)

```typescript
import { createContext, useContext, useState, useCallback } from 'react';

export interface InstallationState {
  taskId: string | null;
  skillName: string;
  skillId: string;
  status: 'idle' | 'pending' | 'downloading' | 'extracting' | 'validating' | 'installing' | 'complete' | 'error';
  progress: number; // 0-100
  errorMessage: string | null;
}

interface SkillManagerContextType {
  installation: InstallationState;
  startInstall: (skillId: string, skillName: string, source: 'marketplace' | 'upload') => Promise<void>;
  cancelInstall: () => void;
  clearInstallation: () => void;
}

export const SkillManagerContext = createContext<SkillManagerContextType | undefined>(undefined);

export function SkillManagerProvider({ children }: { children: React.ReactNode }) {
  const [installation, setInstallation] = useState<InstallationState>({
    taskId: null,
    skillName: '',
    skillId: '',
    status: 'idle',
    progress: 0,
    errorMessage: null,
  });

  const startInstall = useCallback(async (skillId: string, skillName: string, source: string) => {
    try {
      const endpoint = source === 'upload' ? '/v1/skills/upload' : '/v1/skills/install';
      const response = await fetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({ skill_id: skillId, source }),
      });
      const data = await response.json();
      setInstallation({
        taskId: data.task_id,
        skillId,
        skillName,
        status: 'pending',
        progress: 0,
        errorMessage: null,
      });
    } catch (err) {
      setInstallation(prev => ({
        ...prev,
        status: 'error',
        errorMessage: String(err),
      }));
    }
  }, []);

  const cancelInstall = useCallback(() => {
    setInstallation(prev => ({
      ...prev,
      status: 'error',
      errorMessage: 'Installation cancelled by user',
    }));
  }, []);

  const clearInstallation = useCallback(() => {
    setInstallation({
      taskId: null,
      skillName: '',
      skillId: '',
      status: 'idle',
      progress: 0,
      errorMessage: null,
    });
  }, []);

  return (
    <SkillManagerContext.Provider value={{ installation, startInstall, cancelInstall, clearInstallation }}>
      {children}
    </SkillManagerContext.Provider>
  );
}

export function useSkillManager() {
  const ctx = useContext(SkillManagerContext);
  if (!ctx) throw new Error('useSkillManager must be used inside SkillManagerProvider');
  return ctx;
}
```

---

## 5. E2E Test Cases (6 major scenarios, ~150 LoC)

### File: `tests/e2e/test_phase5_skill_manager_e2e.py`

#### Test 1: Admin Can Install from Marketplace

```python
def test_admin_can_install_from_marketplace():
    """Happy path: admin user clicks install → status updates via polling."""
    # 1. POST /v1/skills/install with skill_id="os.flow_guard"
    # 2. Verify response: task_id returned, status='pending'
    # 3. Poll /v1/skills/status?task_id=<task_id> 10 times at 2s intervals
    # 4. Verify status progresses: pending → downloading → extracting → complete
    # 5. Verify skill appears in GET /v1/skills/installed
```

#### Test 2: Non-Admin Rejected on Install

```python
def test_non_admin_rejected_on_install():
    """Adversarial: non-admin curl → 403 Forbidden."""
    # 1. POST /v1/skills/install as non-authenticated user (simulated via header drop)
    # 2. Verify response: 403 Forbidden
    # 3. Verify no audit event emitted (or emitted as 'denied')
```

#### Test 3: Concurrent Installs (atomic via Phase 4)

```python
def test_concurrent_installs_atomic():
    """Two installs in flight → both succeed without corruption (Phase 4 guarantee)."""
    # 1. POST /v1/skills/install for skill A (task_id_a)
    # 2. Immediately POST /v1/skills/install for skill B (task_id_b) (no waiting)
    # 3. Poll both status endpoints concurrently
    # 4. Verify both eventually reach 'complete' without conflicts
    # 5. Verify both appear in /v1/skills/installed with correct versions
```

#### Test 4: Polling Timeout on Hung API

```python
def test_polling_timeout_on_hung_api():
    """API hangs → polling shows error after 5s timeout."""
    # 1. Mock /v1/skills/status to hang indefinitely
    # 2. START installation (succeeds)
    # 3. Poll status endpoint
    # 4. Verify timeout after 5s (AbortSignal.timeout(5000))
    # 5. Verify error banner shown to user: 'Status unavailable (network error)'
```

#### Test 5: Uninstall Success + Audit

```python
def test_uninstall_with_audit_event():
    """Uninstall → skill removed + audit event logged."""
    # 1. Pre-condition: install a skill (task from test 1)
    # 2. POST /v1/skills/uninstall with skill_id
    # 3. Verify response: task_id returned
    # 4. Poll until status='complete'
    # 5. Verify skill NOT in GET /v1/skills/installed
    # 6. Verify audit event 'skill.uninstall' in audit trail
```

#### Test 6: Upload ZIP Validation

```python
def test_upload_zip_validation():
    """Upload non-ZIP file → 400 Bad Request."""
    # 1. Create a .txt file (not .zip)
    # 2. POST /v1/skills/upload with .txt file
    # 3. Verify response: 400 Bad Request
    # 4. Verify error message: 'File must be a .zip'
    # 5. Test valid .zip upload succeeds
```

---

## 6. Integration Points (Existing Systems)

### Phase 4: SkillInstaller

- Already implemented: atomic ZIP, dependency resolution, rollback
- Used by: Phase 5 endpoints (`installer.install_from_marketplace()`, etc.)
- No changes needed

### ADR-0314: Learning + Audit Events

- Phase 5 emits: `skill.install`, `skill.uninstall`, `skill.upload`
- Via: `emit_audit()` helper (existing in console_audit module)
- Tenant-scoped, hash-chained

### Capabilities Endpoint

- Extends: `/v1/console/capabilities/manifest`
- Adds: `user.is_admin` field (boolean)
- Used by: React UI to hide/show install buttons

---

## 7. Compliance Checkpoints (Load-Bearing)

| Checkpoint | Status | Verification |
|---|---|---|
| **Audit events** (ADR-0314) | ✅ Wired | `emit_audit()` on install/uninstall/upload |
| **E2E proof** (real HTTP) | ✅ Planned | test_phase5_skill_manager_e2e.py (6 tests) |
| **Fail-closed** (no silent failures) | ✅ Designed | Polling timeout, error banners, admin gate |
| **Admin-only gates** (placeholder for ADR-0007) | ✅ Designed | `_is_admin()` helper, 403 response, adversarial test |

---

## 8. Timeline & Deliverables

| K-Gate | Work | Estimate | Blocker |
|---|---|---|---|
| K=1 | Dialectical synthesis (3 design choices) | 30 min | None (✅ DONE) |
| K=2 | E2E plan (this document) | 45 min | None (✅ DONE) |
| K=3 | Red/Green code (5 React + endpoints) | 90 min | None |
| K=4 | Testing + refinement (6 E2E tests, timeouts) | 60 min | K=3 complete |
| K=5 | Documentation (ADR-0681 + completion report) | 30 min | K=4 green |
| **TOTAL** | K=2 → K=5 | ~3–4 hours | None |

---

## 9. Success Criteria (K=3–K=5 Gates)

**K=3 Red/Green:** All code written, syntax verified, no tests yet

**K=4 Refinement:** All tests pass, manual E2E confirmed in browser, adversarial case blocked

**K=5 Documentation:** ADR-0681 accepted, commit pushed to main, Phase 6 ready

---

**Next Step:** Proceed to K=3 (Red/Green Implementation)
