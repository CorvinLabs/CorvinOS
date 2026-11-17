"""Executable specification of the personal decoder v2 (self-consistent, self-learning).

Not product code: pure reference models of the three mechanisms the design adds on top of the shipped
decoder (``core/personal_kb/decoder.py``), plus the corpora and the measurement that the LDD loop
(``run_eval.py``) scores them with. Nothing here touches a store, a key or the network.
"""
