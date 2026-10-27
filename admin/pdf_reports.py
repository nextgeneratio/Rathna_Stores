"""Presentation helpers for staff analytics PDF downloads."""
from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(value: Any) -> str:
    return f"LKR {value:,.2f}"


def _section_title(text: str, styles) -> Paragraph:
    return Paragraph(text, styles["Heading2"])


def build_analytics_pdf(report: dict[str, Any]) -> bytes:
    """Build a concise, printable analytics report from already-aggregated data."""
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Rathna Stores analytics report",
    )
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#38251a")
    styles["Title"].fontName = "Helvetica-Bold"
    styles["Heading2"].textColor = colors.HexColor("#704126")
    styles["Heading2"].spaceBefore = 12
    styles["Heading2"].spaceAfter = 6
    styles.add(ParagraphStyle(name="Metric", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=colors.HexColor("#38251a")))
    styles.add(ParagraphStyle(name="MetricLabel", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#7b6558"), spaceAfter=3))
    right = ParagraphStyle("Right", parent=styles["BodyText"], alignment=TA_RIGHT)

    report_range = report["range"]
    story = [
        Paragraph("Rathna Stores", styles["Title"]),
        Paragraph("Staff analytics report", styles["Heading2"]),
        Paragraph(
            f"Reporting period: {report_range['start'].isoformat()} to {report_range['end'].isoformat()}",
            styles["BodyText"],
        ),
        Spacer(1, 8),
    ]
    metric_cells = [
        [Paragraph("PURCHASES", styles["MetricLabel"]), Paragraph("UNIQUE PURCHASERS", styles["MetricLabel"]), Paragraph("GROSS SALES", styles["MetricLabel"]), Paragraph("AVERAGE ORDER VALUE", styles["MetricLabel"])],
        [Paragraph(str(report["order_count"]), styles["Metric"]), Paragraph(str(report["unique_purchasers"]), styles["Metric"]), Paragraph(_money(report["gross_sales"]), styles["Metric"]), Paragraph(_money(report["average_order_value"]), styles["Metric"])],
    ]
    metric_table = Table(metric_cells, colWidths=[43 * mm] * 4)
    metric_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7c7b8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7c7b8")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffdfb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([metric_table, _section_title("Purchase trend", styles)])
    trend_data = [["Date", "Purchases", "Sales"]] + [
        [point["date"], str(point["orders"]), _money(point["sales"])] for point in report["trend"]
    ]
    story.append(_styled_table(trend_data, [62 * mm, 40 * mm, 70 * mm], right))

    story.append(_section_title("Best-selling products", styles))
    if report["best_sellers"]:
        product_data = [["Product", "Units", "Purchases", "Sales", "Stock"]] + [
            [item["product_name"], str(item["units_sold"]), str(item["purchase_count"]), _money(item["sales_value"]), str(item["current_stock"] if item["current_stock"] is not None else "-")]
            for item in report["best_sellers"]
        ]
        story.append(_styled_table(product_data, [57 * mm, 22 * mm, 29 * mm, 42 * mm, 22 * mm], right))
    else:
        story.append(Paragraph("No product sales to rank for this period.", styles["BodyText"]))

    story.append(_section_title("Customer activity", styles))
    activity = report["customer_activity"]
    story.append(Paragraph(
        f"Registered active customers: {activity['registered']}<br/>"
        f"Active purchasers: {activity['active_purchasers']}<br/>"
        f"Repeat purchasers: {activity['repeat_purchasers']}<br/>"
        f"New purchasers: {activity['new_purchasers']}", styles["BodyText"]
    ))
    document.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return output.getvalue()


def _styled_table(data: list[list[str]], widths: list[float], right: ParagraphStyle) -> Table:
    styles = getSampleStyleSheet()
    header = ParagraphStyle("TableHeader", parent=styles["BodyText"], textColor=colors.white, fontName="Helvetica-Bold")
    rendered = [
        [Paragraph(str(value), header if row_index == 0 else (right if column_index else styles["BodyText"])) for column_index, value in enumerate(row)]
        for row_index, row in enumerate(data)
    ]
    table = Table(rendered, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#704126")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d7c7b8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbf6f1")]),
    ]))
    return table


def _page_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#7b6558"))
    canvas.drawString(document.leftMargin, 10 * mm, "Rathna Stores - Internal staff report")
    canvas.drawRightString(A4[0] - document.rightMargin, 10 * mm, f"Page {document.page}")
    canvas.restoreState()
