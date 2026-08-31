"""``python -m corvin_compute`` entry shim."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
