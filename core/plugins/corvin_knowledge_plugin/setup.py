#!/usr/bin/env python3
"""
Setup script for Corvin-Knowledge Claude Code Plugin
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="corvin-knowledge-plugin",
    version="1.0.0",
    author="Shumway + Claude Haiku 4.5",
    author_email="support@corvin-labs.com",
    description="Agentic Knowledge Mesh for Claude Code",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/CorvinLabs/Corvin-Knowledge",
    project_urls={
        "Bug Tracker": "https://github.com/CorvinLabs/Corvin-Knowledge/issues",
        "Documentation": "https://github.com/CorvinLabs/Corvin-Knowledge",
        "Source Code": "https://github.com/CorvinLabs/Corvin-Knowledge",
    },
    license="Apache-2.0",
    py_modules=["plugin"],
    python_requires=">=3.9",
    install_requires=[
        # No external dependencies - uses stdlib only
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-asyncio>=0.20.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Plugins",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Application Frameworks",
        "Topic :: Utilities",
    ],
    keywords="knowledge-management agentic-ai claude-code git distributed-teams",
    include_package_data=True,
)
