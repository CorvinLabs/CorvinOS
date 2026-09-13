"""Pure Python PPTX Generator (no external dependencies)."""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass
from datetime import datetime
from xml.dom import minidom


# ============================================================================
# CONSTANTS
# ============================================================================

PPTX_RELS_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
    <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

PRESENTATION_RELS_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
    <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="notesMasters/notesMaster1.xml"/>
    <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/handoutMaster" Target="handoutMasters/handoutMaster1.xml"/>
    <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>
</Relationships>'''

CONTENT_TYPES_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
    <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
    <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
    <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
    <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
    <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
    <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''


class PurePythonPPTXGenerator:
    """Minimal PPTX generator in pure Python (no external dependencies)."""

    def __init__(self, output_dir: Path | str = Path("/tmp")):
        """Initialize generator."""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.slides = []
        self.slide_count = 0

    def add_title_slide(
        self,
        kicker: str,
        title: str,
        subtitle: str,
        footer: str,
    ) -> None:
        """Add a title slide."""
        slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
    <p:cSld>
        <p:bg>
            <p:bgPr>
                <a:solidFill>
                    <a:srgbClr val="3278C8"/>
                </a:solidFill>
                <a:effectLst/>
            </p:bgPr>
        </p:bg>
        <p:spTree>
            <p:nvGrpSpPr>
                <p:cNvPr id="1" name="Group 1"/>
                <p:cNvGrpSpPr/>
                <p:nvPr/>
            </p:nvGrpSpPr>
            <p:grpSpPr>
                <a:xfrm>
                    <a:off x="0" y="0"/>
                    <a:ext cx="9144000" cy="6858000"/>
                </a:xfrm>
            </p:grpSpPr>

            <!-- Kicker -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="2" name="Kicker"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="l"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="2400" b="0"/>
                            <a:t>{kicker}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>

            <!-- Title -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="3" name="Title"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="c"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="8800" b="1" solidFill="ffffff"/>
                            <a:t>{title}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>

            <!-- Subtitle -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="4" name="Subtitle"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="c"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="3200" solidFill="cccccc"/>
                            <a:t>{subtitle}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>

            <!-- Footer -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="5" name="Footer"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="c"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="1800" solidFill="ff6432"/>
                            <a:t>{footer}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>
        </p:spTree>
    </p:cSld>
    <p:clrMapOvr>
        <a:masterClrMapping/>
    </p:clrMapOvr>
</p:sld>'''
        self.slides.append(slide_xml)
        self.slide_count += 1

    def add_bullets_slide(
        self,
        heading: str,
        bullets: list[str],
    ) -> None:
        """Add a bullets slide."""
        bullet_xml = ""
        for i, bullet in enumerate(bullets):
            bullet_xml += f'''
            <a:p>
                <a:pPr lvl="0"/>
                <a:r>
                    <a:rPr lang="de-DE" sz="2400"/>
                    <a:t>• {bullet}</a:t>
                </a:r>
            </a:p>'''

        slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
    <p:cSld>
        <p:bg>
            <p:bgPr>
                <a:solidFill>
                    <a:srgbClr val="FFFFFF"/>
                </a:solidFill>
                <a:effectLst/>
            </p:bgPr>
        </p:bg>
        <p:spTree>
            <p:nvGrpSpPr>
                <p:cNvPr id="1" name="Group 1"/>
                <p:cNvGrpSpPr/>
                <p:nvPr/>
            </p:nvGrpSpPr>
            <p:grpSpPr>
                <a:xfrm>
                    <a:off x="0" y="0"/>
                    <a:ext cx="9144000" cy="6858000"/>
                </a:xfrm>
            </p:grpSpPr>

            <!-- Heading -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="2" name="Heading"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="l"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="4400" b="1" solidFill="3278C8"/>
                            <a:t>{heading}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>

            <!-- Bullets -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="3" name="Bullets"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>{bullet_xml}
                </p:txBody>
            </p:sp>
        </p:spTree>
    </p:cSld>
    <p:clrMapOvr>
        <a:masterClrMapping/>
    </p:clrMapOvr>
</p:sld>'''
        self.slides.append(slide_xml)
        self.slide_count += 1

    def add_generic_slide(
        self,
        heading: str,
        content: str,
    ) -> None:
        """Add a generic content slide."""
        slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
    <p:cSld>
        <p:bg>
            <p:bgPr>
                <a:solidFill>
                    <a:srgbClr val="FFFFFF"/>
                </a:solidFill>
                <a:effectLst/>
            </p:bgPr>
        </p:bg>
        <p:spTree>
            <p:nvGrpSpPr>
                <p:cNvPr id="1" name="Group 1"/>
                <p:cNvGrpSpPr/>
                <p:nvPr/>
            </p:nvGrpSpPr>
            <p:grpSpPr>
                <a:xfrm>
                    <a:off x="0" y="0"/>
                    <a:ext cx="9144000" cy="6858000"/>
                </a:xfrm>
            </p:grpSpPr>

            <!-- Heading -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="2" name="Heading"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:pPr algn="l"/>
                        <a:r>
                            <a:rPr lang="de-DE" sz="4400" b="1" solidFill="3278C8"/>
                            <a:t>{heading}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>

            <!-- Content -->
            <p:sp>
                <p:nvSpPr>
                    <p:cNvPr id="3" name="Content"/>
                    <p:cNvSpPr/>
                    <p:nvPr/>
                </p:nvSpPr>
                <p:spPr/>
                <p:txBody>
                    <a:bodyPr/>
                    <a:lstStyle/>
                    <a:p>
                        <a:r>
                            <a:rPr lang="de-DE" sz="2000"/>
                            <a:t>{content}</a:t>
                        </a:r>
                    </a:p>
                </p:txBody>
            </p:sp>
        </p:spTree>
    </p:cSld>
    <p:clrMapOvr>
        <a:masterClrMapping/>
    </p:clrMapOvr>
</p:sld>'''
        self.slides.append(slide_xml)
        self.slide_count += 1

    async def save(self, output_filename: str) -> dict[str, Any]:
        """Save presentation as PPTX (ZIP archive)."""
        output_path = self.output_dir / output_filename

        try:
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as pptx:
                # Write [Content_Types].xml
                pptx.writestr('[Content_Types].xml', CONTENT_TYPES_TEMPLATE)

                # Write _rels/.rels
                pptx.writestr('_rels/.rels', PPTX_RELS_TEMPLATE)

                # Write ppt/_rels/presentation.xml.rels
                pptx.writestr('ppt/_rels/presentation.xml.rels', PRESENTATION_RELS_TEMPLATE)

                # Write presentation.xml
                presentation_xml = self._generate_presentation_xml()
                pptx.writestr('ppt/presentation.xml', presentation_xml)

                # Write slides
                for i, slide_xml in enumerate(self.slides, 1):
                    pptx.writestr(f'ppt/slides/slide{i}.xml', slide_xml)

                # Write docProps/core.xml
                core_xml = self._generate_core_properties()
                pptx.writestr('docProps/core.xml', core_xml)

                # Write docProps/app.xml
                app_xml = self._generate_app_properties()
                pptx.writestr('docProps/app.xml', app_xml)

            return {
                "status": "success",
                "output_path": str(output_path),
                "slide_count": self.slide_count,
                "file_size_bytes": output_path.stat().st_size,
                "created_at": datetime.utcnow().isoformat() + "Z",
            }

        except Exception as e:
            return {
                "status": "blocked",
                "error": str(e),
            }

    def _generate_presentation_xml(self) -> str:
        """Generate presentation.xml."""
        slide_ids = ""
        for i in range(1, self.slide_count + 1):
            slide_ids += f'<p:sldId id="{255 + i}" r:id="rId{i}"/>\n'

        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                showMasterSp="1">
    <p:sldSz cx="9144000" cy="6858000"/>
    <p:notesSz cx="7772400" cy="5828400"/>
    <p:sldIdLst>
{slide_ids}    </p:sldIdLst>
    <p:hfPr/>
</p:presentation>'''

    def _generate_core_properties(self) -> str:
        """Generate docProps/core.xml."""
        timestamp = datetime.utcnow().isoformat() + "Z"
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/officeDocument/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>CorvinOS Video</dc:title>
    <dc:subject>Professional Presentation</dc:subject>
    <dc:creator>CorvinOS Video Producer</dc:creator>
    <cp:lastModifiedBy>CorvinOS</cp:lastModifiedBy>
    <cp:created>{timestamp}</cp:created>
    <cp:modified>{timestamp}</cp:modified>
</cp:coreProperties>'''

    def _generate_app_properties(self) -> str:
        """Generate docProps/app.xml."""
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
    <TotalTime>0</TotalTime>
    <Application>CorvinOS Video Producer</Application>
    <Slides>{self.slide_count}</Slides>
    <HiddenSlides>0</HiddenSlides>
    <MMClips>0</MMClips>
    <ScaleCrop>false</ScaleCrop>
    <HeadingPairs>
        <vt:vector baseType="variant" size="2">
            <vt:variant><vt:lpstr>Folienlayouts</vt:lpstr></vt:variant>
            <vt:variant><vt:i4>1</vt:i4></vt:variant>
        </vt:vector>
    </HeadingPairs>
    <TitlesOfParts>
        <vt:vector baseType="lpstr" size="{self.slide_count}">
            {''.join([f'<vt:lpstr>Folie {i}</vt:lpstr>' for i in range(1, self.slide_count + 1)])}
        </vt:vector>
    </TitlesOfParts>
</Properties>'''
