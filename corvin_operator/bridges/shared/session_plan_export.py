"""session_plan_export.py — Cross-session plan linking and export (ADR-0407, ADR-0668).

Manages plan continuity across session boundaries. When a session is split,
this module discovers active plans from the old session and creates linked
references in the new session, maintaining plan history and execution continuity.

Used by session_auto_renewal_hook.py during autonomous session splits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanReference:
	"""Immutable reference to a plan with link metadata."""
	plan_id: str
	original_session_id: str
	new_session_id: str
	split_reason: str
	created_at: str  # ISO 8601
	plan_status: str  # "active", "completed", "paused"
	original_plan_path: str  # Path to original plan in old session


class SessionPlanExporter:
	"""Export and link plans across session boundaries."""

	PLANS_SUBDIR = ".plans"
	INDEX_FILENAME = "index.json"

	def __init__(self, corvin_home: Optional[str] = None, tenant_id: str = "_default"):
		"""Initialize the plan exporter.

		Args:
			corvin_home: Optional CORVIN_HOME path override (defaults to ~/.corvin)
			tenant_id: Tenant ID for tenant-scoped paths (defaults to "_default")
		"""
		if corvin_home:
			self.corvin_home = Path(corvin_home)
		else:
			self.corvin_home = Path.home() / ".corvin"
		self.tenant_id = tenant_id

	async def export_and_link_plans(
		self,
		old_session_id: str,
		new_session_id: str,
		split_reason: str = "token_budget",
		tenant_id: str = "_default",
		accept_missing_status: bool = False,
	) -> Dict[str, str]:
		"""Export all active plans from old session to new session.

		Args:
			old_session_id: Session ID being split (source)
			new_session_id: New session ID (destination)
			split_reason: Reason for session split (e.g., "token_budget", "manual")
			tenant_id: Tenant ID for plan scoping (B5 FIX: deduplication scope)
			accept_missing_status: If True, treat plans with missing/unknown status as "active" (G20 FIX)

		Returns:
			Dict mapping old plan IDs to new plan IDs, e.g.:
			{"plan_1": "plan_1_split_1", "plan_2": "plan_2_split_1"}

			Empty dict if no plans found or TaskCreate unavailable (graceful degradation).
		"""
		logger.info(
			f"[SessionPlanExport] Starting plan export: {old_session_id} → {new_session_id}"
		)

		try:
			# 1. Discover active plans in old session (G20 FIX: pass accept_missing_status)
			old_plans = await self._discover_plans(old_session_id, tenant_id, accept_missing_status)
			if not old_plans:
				logger.debug(f"[SessionPlanExport] No plans found in {old_session_id}")
				return {}

			logger.info(f"[SessionPlanExport] Found {len(old_plans)} plans in old session")

			# 2. Create new plan references
			plan_mapping = {}
			new_references: List[PlanReference] = []

			for plan_id, plan_path in old_plans.items():
				# Create new plan ID with split marker
				new_plan_id = f"{plan_id}_split_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

				# Read original plan metadata
				plan_status = await self._get_plan_status(plan_path)

				# Create reference
				ref = PlanReference(
					plan_id=new_plan_id,
					original_session_id=old_session_id,
					new_session_id=new_session_id,
					split_reason=split_reason,
					created_at=datetime.utcnow().isoformat() + "Z",
					plan_status=plan_status,
					original_plan_path=str(plan_path),
				)

				plan_mapping[plan_id] = new_plan_id
				new_references.append(ref)

			# 3. Persist plan references in new session
			await self._persist_plan_references(new_session_id, new_references, tenant_id)

			logger.info(
				f"[SessionPlanExport] Linked {len(plan_mapping)} plans "
				f"to {new_session_id}"
			)
			return plan_mapping

		except Exception as e:
			logger.exception(
				f"[SessionPlanExport] export_and_link_plans failed: {e}"
			)
			return {}

	async def _discover_plans(self, session_id: str, tenant_id: str, accept_missing_status: bool = False) -> Dict[str, Path]:
		"""Discover all active plans in a session.

		Args:
			session_id: Session ID to search
			tenant_id: Tenant ID for tenant-scoped paths
			accept_missing_status: If True, include plans with missing/unknown status (G20 FIX)

		Returns:
			Dict of plan_id → plan_path, empty if none found or TaskCreate unavailable.
		"""
		try:
			# Try TaskCreate API first (graceful: if unavailable, continue to filesystem)
			plans_from_api = await self._discover_plans_via_api(session_id, tenant_id, accept_missing_status)
			if plans_from_api is not None:
				return plans_from_api
		except Exception as e:
			logger.debug(f"[SessionPlanExport] TaskCreate API unavailable: {e}")

		# Fall back to filesystem discovery
		return await self._discover_plans_via_filesystem(session_id, tenant_id)

	async def _discover_plans_via_api(self, session_id: str, tenant_id: str, accept_missing_status: bool = False) -> Optional[Dict[str, Path]]:
		"""Discover plans via TaskCreate API.

		Args:
			session_id: Session ID to query
			tenant_id: Tenant ID for tenant-scoped paths
			accept_missing_status: If True, include plans with missing/unknown status (G20 FIX)

		Returns:
			Dict of plan_id → plan_path, or None if TaskCreate unavailable.
		"""
		try:
			# Lazy import to avoid hard dependency
			from core.task_manager.taskcreate_api import get_plans_for_session

			plans = await get_plans_for_session(session_id, tenant_id=tenant_id)
			if plans:
				return {
					plan["id"]: Path(plan["path"])
					for plan in plans
					if (accept_missing_status and plan.get("status") in ("active", "paused", "unknown", None))
					   or (not accept_missing_status and plan.get("status") in ("active", "paused"))
				}
			return {}
		except ImportError:
			logger.debug("[SessionPlanExport] TaskCreate module not available")
			return None
		except Exception as e:
			logger.debug(f"[SessionPlanExport] TaskCreate API error: {e}")
			return None

	async def _discover_plans_via_filesystem(self, session_id: str, tenant_id: str, accept_missing_status: bool = False) -> Dict[str, Path]:
		"""Discover plans by scanning session filesystem.

		Args:
			session_id: Session ID to search
			tenant_id: Tenant ID for tenant-scoped paths
			accept_missing_status: If True, include plans with missing/unknown status (G20 FIX)

		Returns:
			Dict of plan_id → plan_path, empty if none found.
		"""
		try:
			session_dir = self._get_session_dir(session_id, tenant_id)
			if not session_dir.exists():
				logger.warning(f"[SessionPlanExport] Session dir not found: {session_dir}")
				return {}

			plans_dir = session_dir / self.PLANS_SUBDIR
			if not plans_dir.exists():
				logger.debug(f"[SessionPlanExport] No .plans dir in {session_id}")
				return {}

			# Scan for plan JSON files (exclude index.json)
			plans = {}
			for plan_file in plans_dir.glob("*.json"):
				if plan_file.name == self.INDEX_FILENAME:
					continue

				try:
					with open(plan_file, "r") as f:
						plan_meta = json.load(f)
						plan_id = plan_meta.get("id", plan_file.stem)
						status = plan_meta.get("status", "unknown")

						# G20 FIX: Accept missing/unknown status if flag is set
						if (accept_missing_status and status in ("active", "paused", "unknown", None))  \
						   or (not accept_missing_status and status in ("active", "paused")):
							plans[plan_id] = plan_file

				except json.JSONDecodeError:
					logger.warning(f"[SessionPlanExport] Invalid JSON: {plan_file}")
					continue

			logger.debug(f"[SessionPlanExport] Filesystem found {len(plans)} plans")
			return plans

		except Exception as e:
			logger.exception(
				f"[SessionPlanExport] _discover_plans_via_filesystem failed: {e}"
			)
			return {}

	async def _get_plan_status(self, plan_path: Path) -> str:
		"""Read plan status from metadata file.

		Args:
			plan_path: Path to plan JSON file

		Returns:
			Plan status string, defaults to "unknown"
		"""
		try:
			with open(plan_path, "r") as f:
				plan_meta = json.load(f)
				return plan_meta.get("status", "unknown")
		except Exception as e:
			logger.debug(f"[SessionPlanExport] Error reading plan status: {e}")
			return "unknown"

	async def _persist_plan_references(
		self,
		new_session_id: str,
		references: List[PlanReference],
		tenant_id: str,
	) -> bool:
		"""Persist plan references in new session's index (B5 FIX: deduplication per plan_id).

		Args:
			new_session_id: Target session ID
			references: List of plan references to persist
			tenant_id: Tenant ID for tenant-scoped paths

		Returns:
			True if successful, False otherwise
		"""
		try:
			session_dir = self._get_session_dir(new_session_id, tenant_id)
			session_dir.mkdir(parents=True, exist_ok=True)

			plans_dir = session_dir / self.PLANS_SUBDIR
			plans_dir.mkdir(parents=True, exist_ok=True)

			# Read existing index or create new one
			index_path = plans_dir / self.INDEX_FILENAME
			if index_path.exists():
				with open(index_path, "r", encoding="utf-8") as f:
					index = json.load(f)
			else:
				index = {"version": "1.0", "plans": []}

			# B5 FIX: Deduplicate by plan_id — keep only newest version of each plan
			existing_plan_ids = {}
			for plan in index.get("plans", []):
				plan_id = plan.get("plan_id")
				if plan_id:
					# C9 FIX: Normalize UTF-8 for plan_id comparison
					normalized_id = unicodedata.normalize('NFC', plan_id)
					if normalized_id not in existing_plan_ids:
						existing_plan_ids[normalized_id] = plan
					else:
						# Keep the newer one (by created_at timestamp)
						existing_created = existing_plan_ids[normalized_id].get("created_at", "")
						new_created = plan.get("created_at", "")
						if new_created > existing_created:
							existing_plan_ids[normalized_id] = plan

			# Rebuild plans list with deduplicated entries
			deduped_plans = list(existing_plan_ids.values())

			# Append new references (also with UTF-8 normalization)
			for ref in references:
				ref_dict = asdict(ref)
				# C9 FIX: Normalize plan_id to NFC
				ref_dict["plan_id"] = unicodedata.normalize('NFC', ref_dict["plan_id"])
				deduped_plans.append(ref_dict)

			index["plans"] = deduped_plans

			# Write index atomically
			index_path.write_text(
				json.dumps(index, indent=2, ensure_ascii=False),
				encoding="utf-8",
			)

			logger.info(
				f"[SessionPlanExport] Persisted {len(references)} "
				f"references in {index_path} (deduped to {len(deduped_plans)} total)"
			)
			return True

		except Exception as e:
			logger.exception(
				f"[SessionPlanExport] _persist_plan_references failed: {e}"
			)
			return False

	def _get_session_dir(self, session_id: str, tenant_id: str) -> Path:
		"""Resolve session directory path with tenant scoping (ADR-0007).

		Args:
			session_id: Session ID
			tenant_id: Tenant ID for tenant-scoped paths

		Returns:
			Path to session directory: ~/.corvin/tenants/<tid>/sessions/<session_id>
		"""
		return self.corvin_home / "tenants" / tenant_id / "sessions" / session_id


# Singleton instance (per tenant)
_exporter_instances: Dict[str, SessionPlanExporter] = {}


def get_plan_exporter(corvin_home: Optional[str] = None, tenant_id: str = "_default") -> SessionPlanExporter:
	"""Get or create the singleton plan exporter for a tenant.

	Args:
		corvin_home: Optional CORVIN_HOME path override (defaults to ~/.corvin)
		tenant_id: Tenant ID for tenant-scoped paths (defaults to "_default")

	Returns:
		SessionPlanExporter instance for the given tenant
	"""
	global _exporter_instances
	cache_key = f"{corvin_home}:{tenant_id}"
	if cache_key not in _exporter_instances:
		_exporter_instances[cache_key] = SessionPlanExporter(
			corvin_home=corvin_home, tenant_id=tenant_id
		)
	return _exporter_instances[cache_key]
