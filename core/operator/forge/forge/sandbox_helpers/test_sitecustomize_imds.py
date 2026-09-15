"""R2 regression: loopback-deny shim blocks Oracle/Alibaba cloud IMDS IPv4."""
import importlib.util
from pathlib import Path

_SC = Path(__file__).resolve().parent / "sitecustomize.py"


def _load():
    spec = importlib.util.spec_from_file_location("sc_under_test", _SC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_oracle_alibaba_imds_blocked():
    sc = _load()
    assert sc._ip_is_blocked("192.0.0.192")          # Oracle
    assert sc._ip_is_blocked("100.100.100.200")      # Alibaba
    assert sc._ip_is_blocked("::ffff:192.0.0.192")   # v4-mapped form
    assert sc._ip_is_blocked("169.254.169.254")      # AWS/GCP/Azure (link-local)


def test_public_ip_not_blocked():
    sc = _load()
    assert not sc._ip_is_blocked("8.8.8.8")
    assert not sc._ip_is_blocked("93.184.216.34")
