"""PDF Export for Maturity Report."""
from fastapi import APIRouter, Depends
from ..deps import require_session
from .api_vibe_maturity import MaturityMeasurementAPI

router = APIRouter(prefix="/vibe/maturity", tags=["console-vibe-export"])

@router.get("/export/pdf")
async def export_maturity_pdf(window: str = "7d", rec=Depends(require_session)):
    """Export maturity report as PDF."""
    try:
        import subprocess, tempfile, datetime
        from pathlib import Path

        api = MaturityMeasurementAPI()
        measurements = api.get_measurements(window=window, tenant_id=rec.tenant_id)

        if not measurements:
            return {"error": "No data available"}

        # Generate HTML report
        html = f"""
        <html><head><style>
        body {{ font-family: Arial; margin: 40px; color: #333; }}
        h1 {{ color: #0D47A1; }} .stat {{ display: inline-block; margin: 20px; }}
        .grid {{ display: grid; grid-cols: 4; gap: 20px; }}
        </style></head><body>
        <h1>9D Maturity Report</h1>
        <p>Generated: {datetime.datetime.now().isoformat()}</p>
        <p>Window: {window} | Measurements: {len(measurements)}</p>
        <div class="grid">
        {f'<div class="stat"><strong>Data Points:</strong> {len(measurements)}</div>'}
        {f'<div class="stat"><strong>Time Range:</strong> {measurements[0].get("timestamp", "N/A")} to {measurements[-1].get("timestamp", "N/A")}</div>'}
        </div>
        <p><em>Full detailed report available in console dashboard.</em></p>
        </body></html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(html)
            html_path = f.name

        pdf_path = html_path.replace('.html', '.pdf')
        try:
            subprocess.run(['wkhtmltopdf', html_path, pdf_path], check=True, timeout=10)
            with open(pdf_path, 'rb') as pdf:
                return {"pdf": pdf.read().hex(), "filename": f"maturity-{window}.pdf"}
        except:
            return {"html": html, "filename": f"maturity-{window}.html"}
    except Exception as e:
        return {"error": str(e)}
