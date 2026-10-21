"""Test-install only: pin the licence tier resolver to "member" for the A2A network gate.

Mirrors corvin_operator/bridges/shared/a2a_test_tier.pin_member_tier (the repo's own A2A E2E
fixture). Loaded through an extra PYTHONPATH entry from a systemd drop-in; never part of the
product. capability_api.active_tier and validator.active_tier are replaced, lazily, once the module is imported —
the member LIMITS apply (no daily compute pool, no peer cap); HMAC, pairing and every other gate run as shipped.
"""
import importlib.abc
import importlib.util
import sys

# The licence package is importable under TWO names in the console process — ``corvin_operator.license``
# (the gate) and bare ``license`` (``a2a_worker`` → ``license.compute_quota``; ``corvin_operator`` is on
# sys.path) — and each name is its own module object, so both are pinned.
_TARGETS = {"corvin_operator.license.capability_api", "corvin_operator.license.validator",
            "license.capability_api", "license.validator"}


class _MemberPin(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name not in _TARGETS:
            return None
        _TARGETS.discard(name)
        if not _TARGETS:
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
        spec = importlib.util.find_spec(name)
        if spec is None or spec.loader is None:
            return None
        loader = spec.loader
        orig_exec = loader.exec_module

        def exec_module(module):
            orig_exec(module)
            module.active_tier = lambda *_a, **_k: "member"
            if name.endswith(".validator"):
                # The limits too (get_limit resolves the licence dict, not the tier): a member
                # install has no daily compute pool — on the Free pool the 11th worker run of
                # the day answers `quota`, which an E2E with ~12 real runs must not hit.
                orig_get_limit = module.get_limit

                def get_limit(feature, _orig=orig_get_limit):
                    try:
                        from corvin_operator.license import (
                            limits as _limits,  # same table under both names
                        )
                        member = _limits.TIER_RESOURCE_LIMITS["member"]
                        if feature in member:
                            return member[feature]
                    except Exception:  # noqa: BLE001 — unknown feature: the shipped answer
                        pass
                    return _orig(feature)

                module.get_limit = get_limit

        loader.exec_module = exec_module
        return spec


sys.meta_path.insert(0, _MemberPin())
