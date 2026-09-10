#!/usr/bin/env python3
"""Fix font sizes and text alignment in CorvinOS SVG diagrams."""

import re
from pathlib import Path

# My newly created diagrams
DIAGRAMS = [
    "docs/diagrams/corvinOS_routing_decision.svg",
    "docs/diagrams/corvinOS_6d_hexagon.svg",
    "docs/diagrams/corvinOS_4layer_acp.svg",
    "docs/diagrams/corvinOS_feedback_loop.svg",
    "docs/diagrams/corvinOS_os_concept_3d.svg",
    "docs/diagrams/corvinOS_token_cost_3d.svg",
    "docs/diagrams/corvinOS_complete_system_3d.svg",
    "docs/diagrams/corvinOS_request_flow_3d.svg",
    "docs/diagrams/corvinOS_convergence_timeline_3d.svg",
    "docs/diagrams/corvinOS_cost_quality_pareto_3d.svg",
]

def fix_svg(svg_path):
    """Fix font sizes and alignment in SVG."""
    with open(svg_path, 'r') as f:
        content = f.read()

    original = content

    # Increase font-size for titles (from 28-36 to 40+)
    content = re.sub(r'font-size="(28|32|36)"', lambda m: f'font-size="{int(m.group(1)) + 8}"', content)

    # Increase font-size for regular text (from 10-14 to 14-18+)
    content = re.sub(r'font-size="([0-9]+)"(?=[^>]*text-anchor)',
                    lambda m: f'font-size="{max(18, int(m.group(1)) + 4)}"', content)

    # Increase font-size for small text (from 9-11 to 12-14)
    content = re.sub(r'font-size="([0-9]{1,2})"',
                    lambda m: f'font-size="{max(14, int(m.group(1)) + 3)}"' if int(m.group(1)) < 12 else f'font-size="{m.group(1)}"',
                    content)

    # Add better spacing for text elements
    content = re.sub(r'<text([^>]*?)>', r'<text\1 letter-spacing="0.5">', content)

    if content != original:
        with open(svg_path, 'w') as f:
            f.write(content)
        print(f"✅ Fixed {svg_path}")
        return True
    else:
        print(f"⏭️  No changes needed for {svg_path}")
        return False

if __name__ == "__main__":
    fixed_count = 0
    for diagram in DIAGRAMS:
        if Path(diagram).exists():
            if fix_svg(diagram):
                fixed_count += 1
        else:
            print(f"❌ Not found: {diagram}")

    print(f"\n✅ Fixed {fixed_count}/{len(DIAGRAMS)} diagrams")
