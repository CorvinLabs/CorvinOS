# CorvinOS Marketplace (ADR-0511)

Plugin-Zentral Repository für CorvinOS Plugins.

## Struktur

```
marketplace/
├── buildin/          → 5 Core-Plugins (Memory, Security, Data, Observability, Integration)
├── contributor/      → 5+ Community-Plugins
├── docs/
│   ├── marketplace/index.json
│   └── stats/dashboard.html
└── README.md
```

## Plugins

### Buildin (Kern-System)
- **memory-plugin** — Vector embeddings & semantic search
- **security-compliance** — Audit, secrets, compliance gates
- **data-processing** — CSV/JSON/Parquet processing
- **observability** — Metrics, logs, tracing
- **integration-hub** — APIs, webhooks, connectors

### Community (Beiträge)
- **nlp-toolkit** — NLP processing, sentiment analysis
- **sql-expert** — SQL optimization, query analysis
- **cloud-deployer** — AWS, GCP, Azure, K8s
- **document-analyzer** — PDF, Word, Excel, OCR
- **web-scraper** — Web scraping, HTML parsing

## GitHub Pages

- Marketplace: https://corvinlabs.github.io/Corvin-Marketplace/
- Stats: https://corvinlabs.github.io/Corvin-Marketplace/stats/

## Architecture

ADR-0511: Plugin-First Marketplace Redesign
