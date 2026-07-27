"""ACO Nervous System — selbsterweiterbare Signal-Infrastruktur (ADR-0177).

Das Nervensystem von CorvinOS: ein einheitliches Protokoll, das alle Layer
und Konnektoren automatisch erfasst und es dem Nutzer ermöglicht, neue
Schichten anzuschließen ohne Kerncode zu ändern.

Drei-Tier-Discovery:
  Tier 0  Built-in Fibers       — immer verfügbar, frische Installation
  Tier 1  Entry-Point-Fibers    — installierte Pakete (corvinOS.nerve_fibers)
  Tier 2  Lokale Plugin-Fibers  — ~/.corvin/nerve_fibers/*.py (kein Packaging nötig)

Erweiterung durch den Nutzer:
  Option A — Paket installieren PLUS deklarieren (opt-in, ADR-0030 parity):
      In setup.py/pyproject.toml:
          [project.entry-points."corvinOS.nerve_fibers"]
          my_layer = "my_package.nerve:MyLayerFiber"

      An installed entry point is NOT on its own a reason to import a package:
      a transitive dependency can declare one without the operator ever seeing
      it.  The declaration lives in ``tenant.corvin.yaml`` and takes either
      shape (see :meth:`NerveRegistry._entry_point_policy`):

          spec:
            nerve:
              fibers: ["my_layer"]              # import exactly these
              # or
              auto_discover_entry_points: true  # import every declared one

      With neither present the entry points are enumerated (so the operator can
      see what is installed) but never loaded.

  Option B — Lokale Datei ablegen (kein Packaging):
      ~/.corvin/nerve_fibers/my_connector.py
          from corvin_console.aco.nerve import NerveFiber, NerveSignal, register_fiber

          @register_fiber
          class MyConnectorFiber:
              fiber_id = "my.connector"
              fiber_version = "1.0.0"

              def scan(self) -> list[NerveSignal]: ...

  Option C — Runtime-Registrierung:
          from corvin_console.aco.nerve import register_fiber
          register_fiber(MyFiber())

Jede Fiber emittiert `NerveSignal`-Objekte — das einheitliche Schema für
alle Gesundheitssignale im System, unabhängig von Layer oder Konnektor.

Contract:
  * scan() NEVER blockiert > 10 s.
  * scan() NEVER raises — degradiert still.
  * Alle Signale fließen in die Audit-Chain und den Boot-Healer.
  * Fiber-Registrierung ist idempotent (same fiber_id = überschreiben).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import logging
import os
import stat as _stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Einheitliches Signal-Schema ───────────────────────────────────────────────

SEVERITY_OK       = "OK"
SEVERITY_LOW      = "LOW"
SEVERITY_MEDIUM   = "MEDIUM"
SEVERITY_HIGH     = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

_SEVERITY_ORDER = {
    SEVERITY_OK:       5,
    SEVERITY_LOW:      4,
    SEVERITY_MEDIUM:   3,
    SEVERITY_HIGH:     2,
    SEVERITY_CRITICAL: 1,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class NerveSignal:
    """Einheitliches Gesundheits-Signal von einer Nerve Fiber.

    Das ist das zentrale Datenformat des Nervensystems.
    Jeder Layer, jeder Konnektor, jedes Plugin emittiert NerveSignals.
    """
    fiber_id: str          # "aco.session" | "l16.audit" | "l10.path_gate" | …
    signal_type: str       # "health.ok" | "anomaly.stall" | "integrity.tampered" | …
    severity: str          # SEVERITY_* Konstanten oben
    message: str           # Menschenlesbare Beschreibung
    data: dict = field(default_factory=dict)   # Domänen-spezifische Details
    ts: str = field(default_factory=_now_iso)
    repair_hint: str = ""  # Für den Boot-Healer: was tun?
    # Ob das Signal einen Audit-Event erzeugen soll
    audit: bool = True

    def to_dict(self) -> dict:
        return {
            "fiber_id": self.fiber_id,
            "signal_type": self.signal_type,
            "severity": self.severity,
            "message": self.message,
            "data": self.data,
            "ts": self.ts,
            "repair_hint": self.repair_hint,
        }

    @property
    def is_healthy(self) -> bool:
        return self.severity in (SEVERITY_OK, SEVERITY_LOW)

    @property
    def needs_repair(self) -> bool:
        return self.severity in (SEVERITY_HIGH, SEVERITY_CRITICAL)


# ── NerveFiber-Protokoll ──────────────────────────────────────────────────────

class NerveFiber:
    """Basis-Klasse für alle Nerve Fibers.

    Jeder Layer und Konnektor implementiert diese Klasse.
    Der Nutzer kann eigene Fibers erstellen und über drei Wege anschließen:
      - Entry Points (Paket, Option A)
      - Lokale .py-Datei (Option B)
      - register_fiber() Decorator/Funktion (Option C)
    """
    fiber_id: str = "unnamed"
    fiber_version: str = "1.0.0"
    fiber_description: str = ""

    def scan(self) -> list[NerveSignal]:
        """Produziert Gesundheits-Signale. Wird bei jedem Healer-Zyklus aufgerufen.

        Muss innerhalb von ~10 s abschließen. Darf niemals werfen.
        Gibt eine leere Liste zurück wenn alles OK ist (implizites OK-Signal).
        """
        return []

    def repair(self, signal: NerveSignal) -> NerveSignal | None:
        """Versucht Selbstheilung für ein gegebenes Signal.

        Gibt None zurück wenn keine Reparatur möglich ist.
        Gibt ein neues NerveSignal zurück das den Reparatur-Ausgang beschreibt.
        """
        return None

    def describe(self) -> dict:
        return {
            "fiber_id": self.fiber_id,
            "fiber_version": self.fiber_version,
            "fiber_description": self.fiber_description or self.__class__.__doc__ or "",
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class NerveRegistry:
    """Zentrale Registratur aller Nerve Fibers.

    Thread-safe durch GIL (keine atomaren Operationen nötig für dict-Reads).
    Discovery ist einmalig beim ersten `scan_all()` Aufruf (lazy).
    """
    _fibers: dict[str, NerveFiber] = {}
    _discovered: bool = False

    @classmethod
    def register(cls, fiber: NerveFiber) -> None:
        """Registriert eine Fiber. Idempotent bei gleicher fiber_id."""
        cls._fibers[fiber.fiber_id] = fiber
        logger.debug("[Nerve] Fiber registriert: %s v%s", fiber.fiber_id, fiber.fiber_version)

    @classmethod
    def unregister(cls, fiber_id: str) -> None:
        cls._fibers.pop(fiber_id, None)

    @classmethod
    def list_fibers(cls) -> list[dict]:
        return [f.describe() for f in cls._fibers.values()]

    @classmethod
    def scan_all(cls) -> list[NerveSignal]:
        """Führt scan() auf allen registrierten Fibers aus.

        Fail-safe: ein fehlerhafter Fiber erzeugt ein HIGH-Signal,
        blockiert aber keine anderen Fibers.
        """
        if not cls._discovered:
            cls.discover()

        signals: list[NerveSignal] = []
        for fiber in list(cls._fibers.values()):
            try:
                result = fiber.scan()
                signals.extend(result)
            except Exception as exc:
                signals.append(NerveSignal(
                    fiber_id=fiber.fiber_id,
                    signal_type="nerve.fiber_error",
                    severity=SEVERITY_HIGH,
                    message=f"Fiber-Scan fehlgeschlagen: {exc}",
                    data={"error": str(exc)},
                    repair_hint="Fiber-Implementierung prüfen",
                ))
        return signals

    @classmethod
    def repair_all(cls, signals: list[NerveSignal]) -> list[NerveSignal]:
        """Führt repair() auf allen Fibers für Signale aus, die Reparatur benötigen.

        Gibt eine Liste der Reparatur-Ergebnis-Signale zurück.
        """
        repair_results: list[NerveSignal] = []
        for signal in signals:
            if not signal.needs_repair:
                continue
            fiber = cls._fibers.get(signal.fiber_id)
            if fiber is None:
                continue
            try:
                result = fiber.repair(signal)
                if result is not None:
                    repair_results.append(result)
            except Exception as exc:
                logger.debug("[Nerve] Repair-Fehler für %s: %s", signal.fiber_id, exc)
        return repair_results

    @classmethod
    def discover(cls) -> None:
        """Drei-Tier-Discovery: Built-ins → Entry-Points → Lokale Plugins."""
        cls._register_builtins()
        cls._discover_entry_points()
        cls._discover_local_plugins()
        cls._discovered = True
        logger.info("[Nerve] Discovery abgeschlossen — %d Fibers registriert", len(cls._fibers))

    @classmethod
    def reset(cls) -> None:
        """Für Tests: Registry zurücksetzen."""
        cls._fibers.clear()
        cls._discovered = False

    # ── Tier 0: Built-in Fibers ───────────────────────────────────────────────

    @classmethod
    def _register_builtins(cls) -> None:
        """Registriert alle eingebauten Fibers. Immer verfügbar, kein Packaging nötig."""
        try:
            from .nerve_builtins import _BUILTIN_FIBERS
            for fiber in _BUILTIN_FIBERS:
                cls.register(fiber)
        except Exception as exc:
            logger.debug("[Nerve] Built-in-Registrierung fehlgeschlagen: %s", exc)

    # ── Tier 1: Entry-Point-Discovery ─────────────────────────────────────────

    @classmethod
    def _entry_point_policy(cls) -> tuple[bool, set[str]]:
        """Which ``corvinOS.nerve_fibers`` entry points this boot may import.

        Returns ``(load_all, declared_names)``.  Deny-by-default: with no
        declaration at all the answer is ``(False, set())`` and NOTHING is
        imported — same stance as ``spec.plugins.auto_discover_entry_points``
        in the plugin loader, and for the same reason: ``ep.load()`` executes
        third-party module code, so it needs an operator act, and "the package
        happens to be installed" is not one (a transitive dependency can
        declare an entry point the operator never chose).

        Read from ``tenant.corvin.yaml`` of the boot tenant:

        * ``spec.nerve.fibers`` — a list of entry-point names to import.
        * ``spec.nerve.auto_discover_entry_points`` — import every one.
        * ``spec.plugins.auto_discover_entry_points`` — honoured as a fallback
          ONLY when ``spec.nerve`` says nothing.  An operator who already told
          the plugin loader "yes, import entry points nobody listed" has made
          exactly this decision once; making them make it twice would silently
          drop fibers they have today.  It can only ever widen an opt-in that
          is already given, never create one.

        Every failure path resolves to ``(False, set())``: an unreadable config
        must not be a reason to import more code than declared.
        """
        try:
            import yaml  # type: ignore[import-not-found]
            from forge import paths as _fp  # type: ignore[import-not-found]
            from forge.tenants import current_tenant  # type: ignore[import-not-found]

            path = Path(_fp.tenant_global_dir(current_tenant())) / "tenant.corvin.yaml"
            if not path.exists():
                return False, set()
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                return False, set()
            # k8s-style manifest — settings live under spec:, legacy docs are flat.
            spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else doc
            nerve_cfg = spec.get("nerve") if isinstance(spec.get("nerve"), dict) else {}

            declared = {
                str(name)
                for name in (nerve_cfg.get("fibers") or [])
                if isinstance(name, (str, int))
            }
            if "auto_discover_entry_points" in nerve_cfg:
                return bool(nerve_cfg.get("auto_discover_entry_points")), declared
            if declared:
                return False, declared

            plugins_cfg = spec.get("plugins") if isinstance(spec.get("plugins"), dict) else {}
            return bool(plugins_cfg.get("auto_discover_entry_points", False)), set()
        except Exception as exc:  # noqa: BLE001 — unreadable config = no opt-in
            logger.debug("[Nerve] Entry-Point-Policy nicht lesbar (= kein Opt-in): %s", exc)
            return False, set()

    @classmethod
    def _discover_entry_points(cls) -> None:
        """Lädt DEKLARIERTE Fibers von installierten Paketen (corvinOS.nerve_fibers).

        Enumerating the group is free — it only reads installed metadata.
        ``ep.load()`` is not: it imports the declaring package.  So the loop
        below always enumerates and only ever loads what
        :meth:`_entry_point_policy` allows; everything else is logged at debug
        level so ``corvin`` can still tell the operator what is available.
        """
        load_all, declared = cls._entry_point_policy()
        try:
            eps = importlib.metadata.entry_points(group="corvinOS.nerve_fibers")
            for ep in eps:
                if not (load_all or ep.name in declared):
                    logger.debug(
                        "[Nerve] Entry-Point %s installiert, aber nicht deklariert — "
                        "nicht geladen (spec.nerve.fibers / "
                        "spec.nerve.auto_discover_entry_points)", ep.name,
                    )
                    continue
                try:
                    fiber_cls = ep.load()
                    instance = fiber_cls() if isinstance(fiber_cls, type) else fiber_cls
                    cls.register(instance)
                    logger.info("[Nerve] Entry-Point-Fiber geladen: %s → %s",
                                ep.name, instance.fiber_id)
                except Exception as exc:
                    logger.warning("[Nerve] Entry-Point %s konnte nicht geladen werden: %s",
                                   ep.name, exc)
        except Exception as exc:
            logger.debug("[Nerve] Entry-Point-Discovery übersprungen: %s", exc)

    # ── Tier 2: Lokale Plugin-Discovery ──────────────────────────────────────

    @staticmethod
    def _is_operator_owned(path: Path) -> bool:
        """True when *path* plausibly came from the operator and nobody else.

        The Tier-2 contract is "the operator put this file there".  Two things
        are checked, and one deliberately is not:

        * **owner** — ``st_uid`` must be this process's uid or root.  A file
          another local account owns is not the operator's, however it got
          there.
        * **world-writable** — rejected for both the file and its directory.
          A world-writable ``nerve_fibers/`` lets any local account drop a
          ``.py`` that the console then executes at the next boot; that is a
          privilege escalation, not an extension point.
        * **group-writable is NOT rejected.**  Tempting, and wrong here: the
          default umask on Debian/Ubuntu is 002 with user-private groups, so
          ``0o664`` is simply what a file the operator just created looks like.
          A rule that fires on the operator far more often than on an attacker
          does not protect anything — it only teaches people to stop using the
          feature.  Whether the members of a shared group count as "the
          operator" is an administrator's decision that the file mode does not
          record.

        On Windows ``st_uid``/``st_mode`` carry no ACL information, so this
        reports True there and containment stays the only guard (the repo must
        run on Linux, Windows and macOS).
        """
        if os.name == "nt":  # pragma: no cover — POSIX-only permission model
            return True
        try:
            st = path.stat()
        except OSError:
            return False
        if st.st_uid not in (os.getuid(), 0):
            return False
        return not st.st_mode & _stat.S_IWOTH

    @classmethod
    def _discover_local_plugins(cls) -> None:
        """Lädt Fibers aus ~/.corvin/nerve_fibers/*.py.

        Kein Packaging, kein pip-Install nötig. Nutzer legt einfach eine
        Python-Datei ab und die Fiber wird beim nächsten Healer-Zyklus aktiv.

        Putting a file there IS the explicit operator act (the counterpart of
        ``spec.plugins.installed``), so this tier keeps loading without a
        further opt-in.  What it does not keep is trusting the path blindly:

        * the directory itself must be operator-owned and not world-writable —
          otherwise "the operator put it there" is not true;
        * every file must resolve INSIDE that directory, so a symlink cannot
          smuggle in code from ``/tmp``;
        * the whole walk is wrapped, because ``discover()`` runs on the boot
          path and a permission error while listing a directory must not cost
          the operator the built-in fibers that were registered before it.
        """
        try:
            cls._load_local_plugin_dir()
        except Exception as exc:  # noqa: BLE001 — boot must survive a bad dir
            logger.warning("[Nerve] Lokale Plugin-Discovery übersprungen: %s", exc)

    @classmethod
    def _load_local_plugin_dir(cls) -> None:
        try:
            from forge import paths as _fp
            plugins_dir = Path(_fp.corvin_home()) / "nerve_fibers"
        except Exception:
            home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
            plugins_dir = home / "nerve_fibers"

        if not plugins_dir.exists():
            return

        if not cls._is_operator_owned(plugins_dir):
            logger.warning(
                "[Nerve] %s ist nicht operator-eigen oder welt-schreibbar — "
                "lokale Fibers werden NICHT ausgeführt (chmod o-w)", plugins_dir,
            )
            return

        try:
            dir_root = plugins_dir.resolve()
        except OSError:
            return

        for plugin_file in sorted(plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            try:
                resolved = plugin_file.resolve()
            except OSError:
                continue
            if resolved.parent != dir_root:
                logger.warning(
                    "[Nerve] %s zeigt aus %s heraus (→ %s) — nicht ausgeführt",
                    plugin_file.name, plugins_dir, resolved,
                )
                continue
            if not cls._is_operator_owned(resolved):
                logger.warning(
                    "[Nerve] %s ist nicht operator-eigen oder welt-schreibbar — "
                    "nicht ausgeführt", plugin_file.name,
                )
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"corvin_nerve_plugin_{plugin_file.stem}", plugin_file
                )
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
                # Alle NerveFiber-Subklassen in diesem Modul registrieren
                for name in dir(mod):
                    obj = getattr(mod, name)
                    if (isinstance(obj, type) and issubclass(obj, NerveFiber)
                            and obj is not NerveFiber):
                        try:
                            instance = obj()
                            cls.register(instance)
                            logger.info("[Nerve] Lokales Plugin geladen: %s → %s",
                                        plugin_file.name, instance.fiber_id)
                        except Exception as exc:
                            logger.warning("[Nerve] Plugin-Instanz %s/%s fehlgeschlagen: %s",
                                           plugin_file.name, name, exc)
            except Exception as exc:
                logger.warning("[Nerve] Plugin-Datei %s konnte nicht geladen werden: %s",
                               plugin_file.name, exc)


# ── Decorator / Funktions-API für einfache Registrierung ─────────────────────

def register_fiber(fiber_or_cls=None, *, fiber_id: str | None = None):
    """Decorator oder Funktion zur Fiber-Registrierung.

    Kann auf drei Arten verwendet werden:

    Als Klassen-Decorator:
        @register_fiber
        class MyFiber(NerveFiber):
            fiber_id = "my.fiber"
            ...

    Als Decorator mit expliziter ID:
        @register_fiber(fiber_id="my.fiber")
        class MyFiber(NerveFiber):
            ...

    Als Funktion mit einer Instanz:
        register_fiber(MyFiber())
    """
    if fiber_or_cls is None:
        # @register_fiber(fiber_id=...) Variante
        def decorator(cls_or_instance):
            if isinstance(cls_or_instance, type):
                instance = cls_or_instance()
            else:
                instance = cls_or_instance
            if fiber_id is not None:
                instance.fiber_id = fiber_id
            NerveRegistry.register(instance)
            return cls_or_instance
        return decorator

    # Direkte Instanz: register_fiber(MyFiber())
    if isinstance(fiber_or_cls, NerveFiber):
        NerveRegistry.register(fiber_or_cls)
        return fiber_or_cls

    # Klassen-Decorator ohne Argumente: @register_fiber
    if isinstance(fiber_or_cls, type) and issubclass(fiber_or_cls, NerveFiber):
        NerveRegistry.register(fiber_or_cls())
        return fiber_or_cls

    return fiber_or_cls


# ── Signal-Aggregation + Audit-Output ────────────────────────────────────────

def write_signals_to_audit(signals: list[NerveSignal], tenant_id: str = "_default") -> None:
    """Schreibt NerveSignals in die Audit-Chain (hash-chained, GDPR-konform)."""
    critical_or_high = [s for s in signals if s.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH)]
    if not critical_or_high:
        return
    try:
        from corvin_console import audit as console_audit
        for sig in critical_or_high[:20]:  # Sicherheitsobergrenze pro Zyklus
            if not sig.audit:
                continue
            console_audit.action_performed(
                action=f"nerve.signal.{sig.severity.lower()}",
                details={
                    "fiber_id": sig.fiber_id,
                    "signal_type": sig.signal_type,
                    "message": sig.message[:500],
                    "tenant_id": tenant_id,
                },
            )
    except Exception as exc:
        logger.debug("[Nerve] Audit-Schreiben fehlgeschlagen: %s", exc)


def summarize_signals(signals: list[NerveSignal]) -> dict:
    """Liefert eine strukturierte Zusammenfassung aller Signale."""
    return {
        "total": len(signals),
        "critical": sum(1 for s in signals if s.severity == SEVERITY_CRITICAL),
        "high": sum(1 for s in signals if s.severity == SEVERITY_HIGH),
        "medium": sum(1 for s in signals if s.severity == SEVERITY_MEDIUM),
        "low": sum(1 for s in signals if s.severity == SEVERITY_LOW),
        "ok": sum(1 for s in signals if s.severity == SEVERITY_OK),
        "fibers": sorted({s.fiber_id for s in signals}),
        "needs_repair": [s.to_dict() for s in signals if s.needs_repair],
    }
