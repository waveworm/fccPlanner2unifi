from __future__ import annotations

import html
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "quickstart-assets"
OUT_FILE = ROOT / "FCCPlanner2UniFi-Quick-Start.docx"
DASHBOARD_URL = "https://fccplanner2unifi.panga-catla.ts.net/dashboard"

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
EMU_PER_INCH = 914400
PAGE_WIDTH_TWIPS = 12240
PAGE_MARGIN_TWIPS = 720
CONTENT_WIDTH_TWIPS = PAGE_WIDTH_TWIPS - (PAGE_MARGIN_TWIPS * 2)


def _strip_tags(text: str) -> str:
    text = text.replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("<b>", "").replace("</b>", "")
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _run(text: str, *, bold: bool = False, color: str | None = None, size_half_points: int | None = None) -> str:
    run_props: list[str] = []
    if bold:
        run_props.append("<w:b/>")
    if color:
        run_props.append(f"<w:color w:val=\"{escape(color)}\"/>")
    if size_half_points:
        run_props.append(f"<w:sz w:val=\"{size_half_points}\"/>")
    rpr = f"<w:rPr>{''.join(run_props)}</w:rPr>" if run_props else ""
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r>"


def _paragraph(
    text: str,
    *,
    style: str | None = None,
    align: str | None = None,
    spacing_before: int = 0,
    spacing_after: int = 120,
    keep_next: bool = False,
    bold: bool = False,
    color: str | None = None,
) -> str:
    ppr_parts: list[str] = []
    if style:
        ppr_parts.append(f"<w:pStyle w:val=\"{escape(style)}\"/>")
    if align:
        ppr_parts.append(f"<w:jc w:val=\"{escape(align)}\"/>")
    ppr_parts.append(f"<w:spacing w:before=\"{spacing_before}\" w:after=\"{spacing_after}\" w:line=\"276\" w:lineRule=\"auto\"/>")
    if keep_next:
        ppr_parts.append("<w:keepNext/>")
    ppr = f"<w:pPr>{''.join(ppr_parts)}</w:pPr>"
    runs: list[str] = []
    for idx, line in enumerate(_strip_tags(text).split("\n")):
        if idx:
            runs.append("<w:r><w:br/></w:r>")
        if line:
            runs.append(_run(line, bold=bold, color=color))
    if not runs:
        runs.append("<w:r><w:t></w:t></w:r>")
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def _numbered_item(text: str, num_id: int) -> str:
    return (
        "<w:p><w:pPr>"
        "<w:spacing w:before=\"0\" w:after=\"80\" w:line=\"276\" w:lineRule=\"auto\"/>"
        "<w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"%d\"/></w:numPr>"
        "</w:pPr>%s</w:p>"
    ) % (num_id, _run(_strip_tags(text)))


def _page_break() -> str:
    return "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>"


def _box(title: str, body_lines: list[str], *, fill: str, border: str) -> str:
    inner: list[str] = [
        _paragraph(title, style="BoxTitle", spacing_after=60, bold=True, color="0F172A"),
    ]
    for line in body_lines:
        inner.append(_paragraph(line, style="BodyText", spacing_after=60))
    return f"""
<w:tbl>
  <w:tblPr>
    <w:tblW w:w="{CONTENT_WIDTH_TWIPS}" w:type="dxa"/>
    <w:tblBorders>
      <w:top w:val="single" w:sz="12" w:space="0" w:color="{border}"/>
      <w:left w:val="single" w:sz="12" w:space="0" w:color="{border}"/>
      <w:bottom w:val="single" w:sz="12" w:space="0" w:color="{border}"/>
      <w:right w:val="single" w:sz="12" w:space="0" w:color="{border}"/>
    </w:tblBorders>
    <w:tblCellMar>
      <w:top w:w="120" w:type="dxa"/>
      <w:left w:w="160" w:type="dxa"/>
      <w:bottom w:w="120" w:type="dxa"/>
      <w:right w:w="160" w:type="dxa"/>
    </w:tblCellMar>
  </w:tblPr>
  <w:tr>
    <w:tc>
      <w:tcPr>
        <w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>
      </w:tcPr>
      {''.join(inner)}
    </w:tc>
  </w:tr>
</w:tbl>
"""


def _section_banner(title: str) -> str:
    return _box(title, [], fill="EAF2FF", border="9CC3FF").replace(
        _paragraph(title, style="BoxTitle", spacing_after=60, bold=True, color="0F172A"),
        _paragraph(title, style="SectionBanner", spacing_after=0, bold=True, color="0F172A"),
    )


def _image_paragraph(image_name: str, rel_id: str, *, max_width_in: float = 6.65, max_height_in: float = 4.25) -> str:
    path = ASSETS / image_name
    with Image.open(path) as img:
        width_px, height_px = img.size
    scale = min((max_width_in * EMU_PER_INCH) / width_px, (max_height_in * EMU_PER_INCH) / height_px)
    cx = int(width_px * scale)
    cy = int(height_px * scale)
    doc_pr_id = abs(hash((image_name, cx, cy))) % 100000 + 1
    return f"""
<w:p>
  <w:pPr><w:jc w:val="center"/><w:spacing w:before="60" w:after="100"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="{NS_WP}">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:docPr id="{doc_pr_id}" name="{escape(image_name)}"/>
        <a:graphic xmlns:a="{NS_A}">
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic xmlns:pic="{NS_PIC}">
              <pic:nvPicPr>
                <pic:cNvPr id="{doc_pr_id}" name="{escape(image_name)}"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="{rel_id}" xmlns:r="{NS_R}"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>
""".strip()


def _build_content() -> tuple[dict, list[dict]]:
    intro = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "quick_start": [
            "Open Dashboard to confirm the system is healthy, review upcoming events, and see current door status.",
            "Use Schedule Board for the weekly planning view across church spaces and doors.",
            "Use Approve / Deny only for after-hours events that need manual review.",
            "If a specific event needs different door timing, use Event Overrides.",
            "Use Office Hours for recurring office access and office-hours-only closures or extra open days.",
        ],
        "daily": [
            "Start on Dashboard: verify sync is healthy and no urgent approvals are waiting.",
            "Open Schedule Board: review the next 3, 7, or 14 days by area and watch for conflicts.",
            "Keep all event edits in Planning Center: add, cancel, move, or rename events there.",
            "Use this app only for access operations: approvals, door timing overrides, manual access, and office-hours management.",
            "Use Office Hours Calendar Overrides only for front-office closures or extra office-opening windows. They do not cancel Planning Center events.",
        ],
        "meeting_summary": [
            "Planning Center stays in charge of events.",
            "This app stays in charge of doors, approvals, office hours, and operational visibility.",
            "Schedule Board gives staff a campus-level planning view.",
            "Office Hours now includes weekly office hours plus office-hours calendar overrides in one place.",
            "Office-hours overrides do not cancel or change Planning Center event access windows.",
        ],
    }
    sections = [
        {
            "title": "Dashboard",
            "image": "dashboard.png",
            "purpose": "Main operations page for today and upcoming events, door windows, pending approvals, and manual access.",
            "actions": [
                "Review upcoming events and confirm mapped rooms and doors look correct.",
                "Approve or deny after-hours events in the Pending Approval card.",
                "Use Cancel to remove a specific event instance from unlock schedules.",
                "Add temporary manual access windows when needed for special cases.",
            ],
        },
        {
            "title": "Schedule Board",
            "image": "schedule-board.png",
            "purpose": "Weekly planning view for facilities and operations. It shows schedule activity by day, by door, and by saved church zone views.",
            "actions": [
                "Switch between 3, 7, or 14 days depending on the planning horizon.",
                "Use saved views like Sanctuary / Lobby, Gym / Student, or Office to narrow what staff see.",
                "Use the search box to filter by event name, room, or door.",
                "Watch the warning panels for room conflicts and shared door coverage.",
            ],
        },
        {
            "title": "Door Mapping",
            "image": "door-mapping.png",
            "purpose": "Maps Planning Center room names to one or more UniFi Access doors.",
            "actions": [
                "Add or edit room-to-door mappings when rooms are renamed or new spaces are used.",
                "Confirm only rooms with real access-control doors are mapped.",
                "Keep non-door locations unmapped so they are excluded.",
            ],
        },
        {
            "title": "Office Hours",
            "image": "office-hours.png",
            "purpose": "Defines recurring weekly office-access windows and office-hours-only calendar overrides.",
            "actions": [
                "Enable office-hours mode when recurring daily access is needed.",
                "Set open and close times by day and apply only to the intended doors.",
                "Use Office Hours Calendar Overrides for holiday closures, vacation weeks, or extra office-open days.",
                "These overrides affect office-hours access only and do not cancel Planning Center events.",
            ],
        },
        {
            "title": "Event Overrides",
            "image": "event-overrides.png",
            "purpose": "Sets custom open and close times per door for specific event names.",
            "actions": [
                "Use overrides when an event needs different times than default lead and lag behavior.",
                "Configure per-door windows so only selected doors follow custom times.",
                "Review existing overrides regularly to avoid stale rules after event changes.",
            ],
        },
        {
            "title": "General Settings",
            "image": "general-settings.png",
            "purpose": "Controls system-wide behavior such as timezone, sync cadence, location filter, and safe-hours policy.",
            "actions": [
                "Adjust sync intervals and lookahead to match operational needs.",
                "Verify timezone and safe-hours rules so approvals trigger correctly.",
                "Update environment and config values carefully and recheck dashboard results after changes.",
            ],
        },
    ]
    return intro, sections


def build_docx() -> None:
    intro, sections = _build_content()
    image_names = ["dashboard.png"] + [section["image"] for section in sections]
    image_names = list(dict.fromkeys(image_names))
    image_rel_ids = {name: f"rId{i + 1}" for i, name in enumerate(image_names)}
    numbering_rel_id = f"rId{len(image_rel_ids) + 1}"
    styles_rel_id = f"rId{len(image_rel_ids) + 2}"

    body: list[str] = [
        _paragraph("FCC Planner2UniFi Quick Start", style="Title", align="center", spacing_after=60),
        _paragraph("Editable meeting handout for Google Docs", style="Subtitle", align="center", spacing_after=40),
        _paragraph(f"Generated: {intro['generated']}", style="Meta", align="center", spacing_after=20),
        _paragraph(DASHBOARD_URL, style="Meta", align="center", spacing_after=180),
        _section_banner("What This Does"),
        _paragraph(
            "This app pulls Planning Center Calendar events, maps event rooms to UniFi Access doors, and builds or updates door unlock windows for daily church operations.",
            style="BodyText",
            spacing_before=90,
        ),
        _box(
            "Important Rule",
            [
                "Planning Center remains the source of truth for event scheduling, room changes, and event cancellations.",
                "This app is the operations layer for doors, approvals, office hours, schedule visibility, and one-off office-hours overrides.",
            ],
            fill="EFF6FF",
            border="93C5FD",
        ),
        _section_banner("Access Requirement (Tailscale)"),
        _box(
            "Access Steps",
            [
                "Tailscale is required for authentication and access.",
                "1. Accept your Tailscale invite sent to your personal email.",
                "2. Sign in to Tailscale on your device using that same email account.",
                "3. Connect to Tailscale before opening this app.",
                "4. As long as you have internet and are connected to Tailscale, you can access this interface from anywhere.",
            ],
            fill="F8FAFC",
            border="CBD5E1",
        ),
        _section_banner("Quick Start"),
    ]
    body.extend(_numbered_item(item, 1) for item in intro["quick_start"])
    body.extend([
        _section_banner("Recommended Daily Workflow"),
    ])
    body.extend(_numbered_item(item, 1) for item in intro["daily"])
    body.extend([
        _section_banner("Dashboard Overview"),
        _image_paragraph("dashboard.png", image_rel_ids["dashboard.png"], max_width_in=6.6, max_height_in=3.95),
        _section_banner("Meeting Summary"),
    ])
    body.extend(_numbered_item(item, 1) for item in intro["meeting_summary"])
    body.append(
        _box(
            "Simple Rule of Thumb",
            [
                'If someone asks, "Where should I make this change?" the answer should usually be simple:',
                "Event change = Planning Center",
                "Door behavior / office-hours behavior = this app",
            ],
            fill="FEF3C7",
            border="F59E0B",
        )
    )

    for section in sections:
        body.extend([
            _page_break(),
            _section_banner(section["title"]),
            _paragraph(f"What this page is for: {section['purpose']}", style="BodyText", spacing_before=90, spacing_after=110),
            _image_paragraph(section["image"], image_rel_ids[section["image"]], max_width_in=6.6, max_height_in=4.2),
            _paragraph("What users should do here", style="Heading2", spacing_before=80, spacing_after=80),
        ])
        body.extend(_numbered_item(item, 1) for item in section["actions"])

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}">
  <w:body>
    {''.join(body)}
    <w:sectPr>
      <w:pgSz w:w="{PAGE_WIDTH_TWIPS}" w:h="15840"/>
      <w:pgMar w:top="{PAGE_MARGIN_TWIPS}" w:right="{PAGE_MARGIN_TWIPS}" w:bottom="{PAGE_MARGIN_TWIPS}" w:left="{PAGE_MARGIN_TWIPS}" w:header="480" w:footer="480" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
"""

    styles_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{NS_W}">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/>
        <w:sz w:val="22"/>
        <w:color w:val="1F2937"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/><w:sz w:val="22"/><w:color w:val="1F2937"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="120" w:after="90"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="34"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/>
    <w:rPr><w:b/><w:sz w:val="22"/><w:color w:val="475569"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Meta">
    <w:name w:val="Meta"/>
    <w:basedOn w:val="Normal"/>
    <w:rPr><w:sz w:val="18"/><w:color w:val="64748B"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="Heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="120" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="BodyText">
    <w:name w:val="Body Text"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:sz w:val="22"/><w:color w:val="1F2937"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="BoxTitle">
    <w:name w:val="Box Title"/>
    <w:basedOn w:val="Normal"/>
    <w:rPr><w:b/><w:sz w:val="22"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="SectionBanner">
    <w:name w:val="Section Banner"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="0"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
</w:styles>
"""

    numbering_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{NS_W}">
  <w:abstractNum w:abstractNumId="0">
    <w:multiLevelType w:val="singleLevel"/>
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/>
      <w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1."/>
      <w:lvlJc w:val="left"/>
      <w:pPr><w:ind w:left="540" w:hanging="300"/></w:pPr>
      <w:rPr><w:b/></w:rPr>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>
"""

    document_rels = [
        f'<Relationship Id="{numbering_rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>',
        f'<Relationship Id="{styles_rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
    ]
    for name, rel_id in image_rel_ids.items():
        document_rels.append(
            f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{escape(name)}"/>'
        )

    rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{NS_REL}">
  {''.join(document_rels)}
</Relationships>
"""

    root_rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{NS_REL}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>FCC Planner2UniFi Quick Start</dc:title>
  <dc:creator>OpenAI Codex</dc:creator>
  <cp:lastModifiedBy>OpenAI Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:modified>
</cp:coreProperties>
"""

    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Microsoft Office Word</Application>
</Properties>
"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT_FILE, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/numbering.xml", numbering_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        for image_name in image_names:
            zf.write(ASSETS / image_name, f"word/media/{image_name}")


if __name__ == "__main__":
    build_docx()
