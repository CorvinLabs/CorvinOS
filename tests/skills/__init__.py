"""Skill test package.

Present so pytest resolves this tree as ``tests.skills.*``. Without it the
rootdir walk stopped at ``tests/skills`` and the sub-package
``tests/skills/video_producer/`` (which HAS an ``__init__.py``) was imported as
the top-level name ``video_producer`` — colliding with the real
``video_producer`` package and failing with
``ModuleNotFoundError: No module named 'video_producer.conftest'``.
"""
