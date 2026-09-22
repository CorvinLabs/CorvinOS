#!/usr/bin/env python3
"""
Generate training PDFs for CorvinOS onboarding.

Outputs:
- training_slides.pdf (20-slide outline)
- daily_checklist.pdf (1 page, print-ready)
- weekly_checklist.pdf (1 page, print-ready)
- monthly_checklist.pdf (1 page, print-ready)

Usage:
  python3 generate_training_pdfs.py
"""

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
import os
from datetime import datetime


def create_training_slides():
    """Generate 20-slide training outline PDF."""
    pdf_path = '/home/shumway/projects/CorvinOS/docs/pdfs/training_slides.pdf'
    doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                          topMargin=0.5*inch, bottomMargin=0.5*inch,
                          leftMargin=0.75*inch, rightMargin=0.75*inch)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1f2937'),
        spaceAfter=0.3*inch,
        fontName='Helvetica-Bold'
    )

    slide_style = ParagraphStyle(
        'SlideTitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#3b82f6'),
        spaceAfter=0.2*inch,
        fontName='Helvetica-Bold'
    )

    content_style = ParagraphStyle(
        'Content',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=0.1*inch,
        leading=14
    )

    story = []

    # Title
    story.append(Paragraph("CorvinOS Training Program", title_style))
    story.append(Paragraph("20 Essential Slides", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    slides = [
        ("Slide 1: Welcome to CorvinOS", [
            "Enterprise AI Operating System",
            "Phase 9: Intent Router + Control Plane",
            "Built on Plugin Architecture"
        ]),
        ("Slide 2: Installation Overview", [
            "System Requirements",
            "Pre-requisites (Python 3.9+, Docker optional)",
            "Windows/Linux/macOS Support"
        ]),
        ("Slide 3: First-Run Setup", [
            "Initialize tenant configuration",
            "Authenticate with Claude API",
            "Configure audit trail"
        ]),
        ("Slide 4: Plugin System Basics", [
            "3 Boot Layers: compliance, core, bundled",
            "Plugin Registry & Lifecycle",
            "Enable/Disable Plugins"
        ]),
        ("Slide 5: Skill System Introduction", [
            "Skills 2.0: Agentic Control Plane",
            "Deterministic + LLM Logic",
            "Self-Learning via Feedback"
        ]),
        ("Slide 6: Cost Tracking & Budgets", [
            "Model Usage Dashboard",
            "Usage Epochs & Counting",
            "Cost Optimization Tips"
        ]),
        ("Slide 7: Learning Loop Activation", [
            "Event Schema (ADR-0314)",
            "Feedback Signals",
            "Optimizer Configuration"
        ]),
        ("Slide 8: Consent & Compliance", [
            "GDPR Art. 5, 6, 32 Enforcement",
            "Consent Gates (L16)",
            "House Rules (L44)"
        ]),
        ("Slide 9: Audit Trail Verification", [
            "Hash-Chain Verification",
            "Boot Tripwire (ADR-0232)",
            "Operator Proof System"
        ]),
        ("Slide 10: Console Web UI", [
            "Dashboard Panels",
            "Settings & Configuration",
            "Real-Time Metrics"
        ]),
        ("Slide 11: API Fundamentals", [
            "/v1/console/* endpoints",
            "A2A (App-to-App) Protocol",
            "Task Envelope & Attestation"
        ]),
        ("Slide 12: Common Workflows", [
            "Install a Skill",
            "Monitor Production",
            "Update Configuration"
        ]),
        ("Slide 13: Troubleshooting 101", [
            "Check audit logs",
            "Verify plugin status",
            "Review error traces"
        ]),
        ("Slide 14: Emergency Hotfixes", [
            "Fail-Closed Gates (L10, L44)",
            "Override Authority",
            "Rollback Procedures"
        ]),
        ("Slide 15: Multi-Tenant Isolation", [
            "Tenant Scope (ADR-0007)",
            "GDPR Compliance",
            "Data Separation"
        ]),
        ("Slide 16: Performance Tuning", [
            "Model Routing (3-tier Selection)",
            "Context Engineering (L10)",
            "Data Flow Guard (L34)"
        ]),
        ("Slide 17: Security Best Practices", [
            "Credential Management",
            "Network Egress Lockdown (L35)",
            "PII Handling"
        ]),
        ("Slide 18: Learning & Optimization", [
            "Feedback Loop Closure (ADR-0613)",
            "Confidence Scoring",
            "Decision History"
        ]),
        ("Slide 19: Community & Marketplace", [
            "Plugin Marketplace",
            "Community Contributions",
            "Skill Repository"
        ]),
        ("Slide 20: Going Live", [
            "Pre-Production Checklist",
            "Production Deployment",
            "Monitoring & Support"
        ])
    ]

    for slide_title, points in slides:
        story.append(Paragraph(slide_title, slide_style))
        for point in points:
            story.append(Paragraph(f"• {point}", content_style))
        story.append(Spacer(1, 0.15*inch))

        # Add page break every 5 slides
        if slides.index((slide_title, points)) % 5 == 4:
            story.append(PageBreak())

    # Footer
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                          styles['Normal']))

    doc.build(story)
    print(f"✅ Created {pdf_path}")


def create_daily_checklist():
    """Generate daily checklist (1 page)."""
    pdf_path = '/home/shumway/projects/CorvinOS/docs/pdfs/daily_checklist.pdf'

    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.75*inch, height - 0.75*inch, "CorvinOS Daily Checklist")

    # Date field
    c.setFont("Helvetica", 10)
    c.drawString(0.75*inch, height - 1.0*inch, f"Date: ________________")

    # Checklist items
    y_pos = height - 1.5*inch
    c.setFont("Helvetica", 11)

    items = [
        "☐ Verify audit chain integrity (corvin audit verify-chain)",
        "☐ Check plugin status (corvin plugin list)",
        "☐ Review error logs for the past 24 hours",
        "☐ Confirm learning loop is active and processing feedback",
        "☐ Check cost dashboard — any anomalies?",
        "☐ Verify all critical skills are responding",
        "☐ Test A2A communication with dependent systems",
        "☐ Confirm no compliance violations in audit trail",
        "☐ Review console alerts and pending tasks",
        "☐ Run daily backup of audit logs",
        "☐ Document any manual interventions required",
        "☐ Schedule any needed updates or maintenance",
    ]

    for item in items:
        c.drawString(0.75*inch, y_pos, item)
        y_pos -= 0.4*inch

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.75*inch, 0.5*inch,
                "Print this checklist and use daily. Keep laminated copy at workstation.")

    c.save()
    print(f"✅ Created {pdf_path}")


def create_weekly_checklist():
    """Generate weekly checklist (1 page)."""
    pdf_path = '/home/shumway/projects/CorvinOS/docs/pdfs/weekly_checklist.pdf'

    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.75*inch, height - 0.75*inch, "CorvinOS Weekly Checklist")

    # Week field
    c.setFont("Helvetica", 10)
    c.drawString(0.75*inch, height - 1.0*inch, f"Week of: ________________")

    # Checklist items
    y_pos = height - 1.5*inch
    c.setFont("Helvetica", 11)

    items = [
        "☐ Audit comprehensive plugin lifecycle (load → execute → unload)",
        "☐ Review skill performance metrics and confidence scores",
        "☐ Test failover of critical plugins",
        "☐ Analyze learning loop convergence rate",
        "☐ Update plugin security patches if available",
        "☐ Review and optimize skill configuration parameters",
        "☐ Backup full audit trail to external storage",
        "☐ Run adversarial testing on security gates (L10, L44)",
        "☐ Test console UI responsiveness and performance",
        "☐ Review GDPR compliance reports",
        "☐ Check tenant isolation for multi-tenant setups",
        "☐ Plan and schedule next maintenance window",
        "☐ Update runbooks with any new procedures discovered",
        "☐ Review and approve pending community plugin PRs",
    ]

    for item in items:
        c.drawString(0.75*inch, y_pos, item)
        y_pos -= 0.36*inch

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.75*inch, 0.5*inch,
                "Print weekly. Schedule 2-3 hours for full checklist completion.")

    c.save()
    print(f"✅ Created {pdf_path}")


def create_monthly_checklist():
    """Generate monthly checklist (1 page)."""
    pdf_path = '/home/shumway/projects/CorvinOS/docs/pdfs/monthly_checklist.pdf'

    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.75*inch, height - 0.75*inch, "CorvinOS Monthly Checklist")

    # Month field
    c.setFont("Helvetica", 10)
    c.drawString(0.75*inch, height - 1.0*inch, f"Month: ________________")

    # Checklist items
    y_pos = height - 1.5*inch
    c.setFont("Helvetica", 11)

    items = [
        "☐ Full audit trail integrity verification (hash-chain validation)",
        "☐ Comprehensive security review of all plugins",
        "☐ Analyze learning loop effectiveness — ROI metrics",
        "☐ Test disaster recovery procedures (full restore from backup)",
        "☐ Performance optimization review — identify bottlenecks",
        "☐ Compliance audit — GDPR, EU AI Act, house rules",
        "☐ Capacity planning — usage trends, growth forecast",
        "☐ Update documentation with latest procedures and known issues",
        "☐ Conduct training session for ops team on new features",
        "☐ Review and optimize cost budgets per tenant",
        "☐ Test failover across multiple zones (if multi-zone setup)",
        "☐ Analyze skill decision quality and feedback loop effectiveness",
        "☐ Plan quarterly roadmap updates and feature releases",
        "☐ Archive logs older than retention period",
    ]

    for item in items:
        c.drawString(0.75*inch, y_pos, item)
        y_pos -= 0.36*inch

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.75*inch, 0.5*inch,
                "Print monthly. Schedule 1 full day for completion. Include full team.")

    c.save()
    print(f"✅ Created {pdf_path}")


if __name__ == '__main__':
    try:
        create_training_slides()
        create_daily_checklist()
        create_weekly_checklist()
        create_monthly_checklist()
        print("\n✅ All 4 PDFs generated successfully!")
    except ImportError as e:
        print(f"Error: reportlab not installed. Install with: pip install reportlab")
        exit(1)
    except Exception as e:
        print(f"Error generating PDFs: {e}")
        exit(1)
