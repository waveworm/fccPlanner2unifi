from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "quickstart-assets"
OUT_FILE = ROOT / "FCCPlanner2UniFi-Quick-Start.pdf"
DASHBOARD_URL = "https://fccplanner2unifi.panga-catla.ts.net/dashboard"


def _scaled_image(path: Path, max_width: float, max_height: float) -> Image:
    img = Image(str(path))
    iw, ih = img.imageWidth, img.imageHeight
    scale = min(max_width / float(iw), max_height / float(ih))
    img.drawWidth = iw * scale
    img.drawHeight = ih * scale
    return img


def build_pdf() -> None:
    doc = SimpleDocTemplate(
        str(OUT_FILE),
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title="FCC Planner to UniFi - Quick Start",
        author="FCC Planner2UniFi",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleLarge",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceAfter=4,
        spaceBefore=8,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#111827"),
    )
    note = ParagraphStyle(
        "Note",
        parent=body,
        backColor=colors.HexColor("#eff6ff"),
        borderColor=colors.HexColor("#93c5fd"),
        borderWidth=0.7,
        borderPadding=8,
        borderRadius=4,
        spaceAfter=8,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=9.25,
        leading=12,
        textColor=colors.HexColor("#374151"),
    )

    full_width = letter[0] - doc.leftMargin - doc.rightMargin
    hero_height = 4.25 * inch
    section_height = 4.45 * inch

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story: list = []

    # Page 1
    story.append(Paragraph("FCC Planner2UniFi Quick Start", title))
    story.append(Paragraph(f"Generated: {generated}", body))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("What This Does", h2))
    story.append(
        Paragraph(
            "This app pulls Planning Center Calendar events, maps event rooms to UniFi Access doors, and builds or updates door unlock windows for daily church operations.",
            body,
        )
    )
    story.append(
        Paragraph(
            "<b>Important rule:</b> Planning Center remains the source of truth for event scheduling, room changes, and event cancellations. "
            "This app is the operations layer for doors, approvals, office hours, schedule visibility, and one-off office-hours overrides.",
            note,
        )
    )

    story.append(Paragraph("Access Requirement (Tailscale)", h2))
    story.append(
        Paragraph(
            "<b>Tailscale is required for authentication and access.</b><br/>"
            "Your Tailscale account is linked to your personal email address.<br/>"
            "1) Accept your Tailscale invite sent to your personal email.<br/>"
            "2) Sign in to Tailscale on your device using that same email account.<br/>"
            "3) Connect to Tailscale before opening this app.<br/>"
            "4) As long as you have internet and are connected to Tailscale, you can access this interface from anywhere.",
            note,
        )
    )
    story.append(Paragraph(f"<b>Web address:</b> {DASHBOARD_URL}", body))
    story.append(Spacer(1, 0.06 * inch))

    story.append(Paragraph("Quick Start", h2))
    steps = [
        "Open <b>Dashboard</b> to confirm the system is healthy, review upcoming events, and see current door status.",
        "Use <b>Schedule Board</b> for the weekly planning view across church spaces and doors.",
        "Use <b>Approve / Deny</b> only for after-hours events that need manual review.",
        "If a specific event needs different door timing, use <b>Event Overrides</b>.",
        "Use <b>Office Hours</b> for recurring office access and office-hours-only closures or extra open days.",
    ]
    story.append(
        ListFlowable(
            [ListItem(Paragraph(s, body), value=i + 1) for i, s in enumerate(steps)],
            bulletType="1",
            leftPadding=12,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=9,
        )
    )
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Recommended Daily Workflow", h2))
    daily = [
        "Start on <b>Dashboard</b>: verify sync is healthy and no urgent approvals are waiting.",
        "Open <b>Schedule Board</b>: review the next 3, 7, or 14 days by area and watch for conflicts.",
        "Keep all event edits in <b>Planning Center</b>: add, cancel, move, or rename events there.",
        "Use this app only for access operations: approvals, door timing overrides, manual access, and office-hours management.",
        "Use <b>Office Hours Calendar Overrides</b> only for front-office closures or extra office-opening windows. They do not cancel Planning Center events.",
    ]
    story.append(
        ListFlowable(
            [ListItem(Paragraph(s, body), value=i + 1) for i, s in enumerate(daily)],
            bulletType="1",
            leftPadding=12,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=9,
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("Dashboard Overview", h2))
    story.append(_scaled_image(ASSETS / "dashboard.png", max_width=full_width, max_height=hero_height))

    sections = [
        {
            "title": "Dashboard",
            "image": ASSETS / "dashboard.png",
            "purpose": "Main operations page for today/upcoming events, door windows, pending approvals, and manual access.",
            "actions": [
                "Review upcoming events and confirm mapped rooms and doors look correct.",
                "Approve or deny after-hours events in the Pending Approval card.",
                "Use Cancel to remove a specific event instance from unlock schedules.",
                "Add temporary/manual access windows when needed for special cases.",
            ],
        },
        {
            "title": "Schedule Board",
            "image": ASSETS / "schedule-board.png",
            "purpose": "Weekly planning view for facilities and operations. It shows upcoming schedule activity by day, by door, and by saved church zone views.",
            "actions": [
                "Switch between 3, 7, or 14 days depending on the planning horizon.",
                "Use saved views like Sanctuary / Lobby, Gym / Student, or Office to narrow what staff see.",
                "Use the search box to filter by event name, room, or door.",
                "Watch the warning panels for room conflicts and shared door coverage.",
            ],
        },
        {
            "title": "Door Mapping",
            "image": ASSETS / "door-mapping.png",
            "purpose": "Maps Planning Center room names to one or more UniFi Access doors.",
            "actions": [
                "Add or edit room-to-door mappings when rooms are renamed or new spaces are used.",
                "Confirm only rooms with real access-control doors are mapped.",
                "Keep non-door locations (homes, online, off-site) unmapped so they are excluded.",
            ],
        },
        {
            "title": "Office Hours",
            "image": ASSETS / "office-hours.png",
            "purpose": "Defines recurring weekly office-access windows and office-hours-only calendar overrides.",
            "actions": [
                "Enable office-hours mode when recurring daily access is needed.",
                "Set open/close times by day and apply only to the intended doors.",
                "Use Office Hours Calendar Overrides for holiday closures, vacation weeks, or extra office-open days.",
                "Remember: these overrides affect office-hours access only and do not cancel Planning Center events.",
            ],
        },
        {
            "title": "Event Overrides",
            "image": ASSETS / "event-overrides.png",
            "purpose": "Sets custom open/close times per door for specific event names.",
            "actions": [
                "Use overrides when an event needs different times than default lead/lag behavior.",
                "Configure per-door windows so only selected doors follow custom times.",
                "Review existing overrides regularly to avoid stale rules after event changes.",
            ],
        },
        {
            "title": "General Settings",
            "image": ASSETS / "general-settings.png",
            "purpose": "Controls system-wide behavior such as timezone, sync cadence, location filter, and safe-hours policy.",
            "actions": [
                "Adjust sync intervals/lookahead to match operational needs.",
                "Verify timezone and safe-hours rules so approvals trigger correctly.",
                "Update environment/config values carefully and recheck dashboard results after changes.",
            ],
        },
    ]

    story.append(PageBreak())
    story.append(Paragraph("Meeting Summary", h2))
    summary_points = [
        "Planning Center stays in charge of events.",
        "This app stays in charge of doors, approvals, office hours, and operational visibility.",
        "Schedule Board gives staff a campus-level planning view.",
        "Office Hours page now includes weekly office hours plus office-hours calendar overrides in one place.",
        "Office-hours overrides do not cancel or change Planning Center event access windows.",
    ]
    story.append(
        ListFlowable(
            [ListItem(Paragraph(point, body), value=i + 1) for i, point in enumerate(summary_points)],
            bulletType="1",
            leftPadding=12,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=9,
        )
    )
    story.append(Spacer(1, 0.12 * inch))
    story.append(
        Paragraph(
            "If someone asks, \"Where should I make this change?\" the answer should usually be simple: "
            "<b>event change = Planning Center</b>, <b>door behavior / office-hours behavior = this app</b>.",
            note,
        )
    )

    for section in sections:
        story.append(PageBreak())
        story.append(Paragraph(section["title"], h2))
        story.append(Paragraph(f"<b>What this page is for:</b> {section['purpose']}", body))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_scaled_image(section["image"], max_width=full_width, max_height=section_height))
        story.append(Spacer(1, 0.12 * inch))
        story.append(Paragraph("<b>What users should do here:</b>", body))
        story.append(
            ListFlowable(
                [ListItem(Paragraph(item, small), value=i + 1) for i, item in enumerate(section["actions"])],
                bulletType="1",
                leftPadding=12,
                bulletFontName="Helvetica-Bold",
                bulletFontSize=9,
            )
        )

    doc.build(story)


if __name__ == "__main__":
    build_pdf()
