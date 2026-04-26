# report_pdf.py
from io import BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import os
from datetime import datetime
from zoneinfo import ZoneInfo  # Manila timestamp

def _coalesce(*vals):
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            continue
        return v
    return None

def _to_float(v, default=0.0):
    try:
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            return float(default)
        return float(s)
    except Exception:
        return float(default)

def _display_fuel_type(row):
    fuel_type = row.get("fuel_type", "")
    fuel_type = "" if fuel_type is None else str(fuel_type).strip()

    if fuel_type == "" or fuel_type.lower() == "nan":
        return "Diesel"

    return fuel_type.title()

def _fmt_money(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "—"

def _draw_paragraph(c, text, style, x, y, max_width):
    p = Paragraph(text, style)
    w, h = p.wrapOn(c, max_width, 1000)
    p.drawOn(c, x, y - h)
    return y - h

def _total_amount_php_from_row(r: dict) -> float:
    """
    Preferred order:
      1) total_dispensed
      2) total_dispensed_php
      3) requested_amount_php + discount_total (or discount_total_php)
    Falls back to 0.0 if nothing usable is present.
    """
    # 1) direct totals
    td = _coalesce(r.get("total_dispensed"))
    if td is not None:
        return _to_float(td, 0.0)

    tdp = _coalesce(r.get("total_dispensed_php"))
    if tdp is not None:
        return _to_float(tdp, 0.0)

    # 2) compute from components
    requested = _to_float(_coalesce(r.get("requested_amount_php")), 0.0)
    # discount_total or discount_total_php (headers you showed include both)
    discount = _to_float(_coalesce(r.get("discount_total"), r.get("discount_total_php")), 0.0)

    return round(requested + discount, 2)

def build_supplier_pdf(*, vouchers, target_station_ids, stations, logo_path=None) -> bytes:
    """
    Supplier Sheet (A4 landscape)

    Columns:
      - Station (Expected)
      - Amount (PHP)
      - Driver name
      - Plate
      - Voucher ID (Unredeemed)
      - Name / Signature
    """
    # Selected stations (match by station name best-effort)
    allowed_ids = set([s for s in target_station_ids if s])
    station_names = {s.get("id"): s.get("name") for s in stations if s.get("id")}

    rows = []
    for r in vouchers or []:
        station_name = (r.get("station") or "").strip()

        include = True
        if allowed_ids:
            include = any(
                (station_names.get(i) or "").strip().lower() == station_name.lower()
                for i in allowed_ids
            )
        if not include:
            continue

        # ── FIX: Amount must be total, not requested ───────────────────────────
        amount = _total_amount_php_from_row(r)
        # ───────────────────────────────────────────────────────────────────────

        driver = r.get("driver_name") or ""
        plate = r.get("vehicle_plate") or ""
        vid = r.get("voucher_id") or ""

        fuel_type = _display_fuel_type(r)

        rows.append([
            station_name,
            fuel_type,
            f"{_fmt_money(amount)}",
            driver,
            plate,
            vid,
            ""  # Type Name & E-Signature
        ])

    # Canvas
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    page_w, page_h = landscape(A4)

    # Tighter left margin; keep top/bottom
    x_margin = 12 * mm   # was 16mm
    y_margin = 14 * mm
    y = page_h - y_margin

    # Styles
    styles = getSampleStyleSheet()
    title_style = styles["Heading2"]
    title_style.spaceAfter = 0
    subtitle_style = styles["Normal"]
    subtitle_style.leading = 14
    faq_heading = ParagraphStyle("FAQHeading", parent=styles["Heading3"], spaceBefore=10, spaceAfter=6)
    faq_q = ParagraphStyle("FAQQ", parent=styles["Heading4"], spaceBefore=8, spaceAfter=2)
    faq_a = ParagraphStyle("FAQA", parent=styles["BodyText"], leading=14, spaceAfter=8)

    # Title/subtitle (left)
    y = _draw_paragraph(c, "UniFleet – Fuel Refuel Vouchers (Daily PDF)", title_style, x_margin, y, page_w - 2*x_margin)
    ts_mnl = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d %H:%M")
    y = _draw_paragraph(c, f"Generated: {ts_mnl}", subtitle_style, x_margin, y, page_w - 2*x_margin)
    y -= 6 * mm

    # Logo (top-right)
    if logo_path and os.path.isfile(logo_path):
        try:
            img = Image(logo_path)
            img._restrictSize(42*mm, 18*mm)
            img_w, img_h = img.drawWidth, img.drawHeight
            img_x = page_w - x_margin - img_w
            img_y = page_h - y_margin - img_h
            img.drawOn(c, img_x, img_y)
        except Exception:
            pass

    # Table (adjusted widths & row height via padding)
    header = [
        "Station",
        "Fuel",
        "Amount (PHP)",
        "Driver Name",
        "Plate",
        "Voucher ID",
        "Type Name & E-Signature<br/><font size=8>Complete & Send Screenshot to Viber</font>"
    ]
    cell_style = ParagraphStyle(
        "SupplierCell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
    )

    header_style = ParagraphStyle(
        "SupplierHeader",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white,
    )

    data = [[Paragraph(str(h), header_style) for h in header]]

    if rows:
        for row in rows:
            data.append([Paragraph(str(cell or ""), cell_style) for cell in row])
    else:
        data.append([Paragraph("—", cell_style) for _ in header])

    # Column widths (fit within A4 landscape minus margins)
    # Totals to ~272mm with 12mm side margins (page width 297mm).
    col_widths = [
        48*mm,  # Station - tighter
        22*mm,  # Fuel
        24*mm,  # Amount
        34*mm,  # Driver - shorter, wraps
        22*mm,  # Plate
        38*mm,  # Voucher ID
        84*mm,  # Type Name & E-Signature - wider
    ]
    table = Table(data, colWidths=col_widths)

    table.setStyle(TableStyle([
        ("FONT", (0,0), (-1,0), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#233b64")),

        # Deep red ONLY for last header cell
        ("BACKGROUND", (-1,0), (-1,0), colors.HexColor("#8B0000")),
        ("TEXTCOLOR", (-1,0), (-1,0), colors.white),

        ("ALIGN", (2,1), (2,-1), "RIGHT"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#d8e2f0")),
        ("ROWBACKGROUNDS", (0,1), (-2,-1), [colors.whitesmoke, colors.HexColor("#f7f9fc")]),
        # Light red signature cells, header remains deep red
        ("BACKGROUND", (-1,1), (-1,-1), colors.HexColor("#FDECEC")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),

        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),

        # Taller voucher rows — designed for about 6 vouchers per page
        ("TOPPADDING", (0,1), (-1,-1), 22),
        ("BOTTOMPADDING", (0,1), (-1,-1), 22),

        # Header
        ("TOPPADDING", (0,0), (-1,0), 7),
        ("BOTTOMPADDING", (0,0), (-1,0), 7),
    ]))

    # Draw table in chunks so rows do not run off the page
    MAX_ROWS_PER_PAGE = 6

    voucher_rows = data[1:]  # exclude header
    chunks = [
        voucher_rows[i:i + MAX_ROWS_PER_PAGE]
        for i in range(0, len(voucher_rows), MAX_ROWS_PER_PAGE)
    ]

    if not chunks:
        chunks = [[Paragraph("—", cell_style) for _ in header]]

    for idx, chunk in enumerate(chunks):
        if idx > 0:
            c.showPage()
            y = page_h - y_margin

            # Redraw title/subtitle on each new page
            y = _draw_paragraph(
                c,
                "UniFleet – Fuel Refuel Vouchers (Daily PDF)",
                title_style,
                x_margin,
                y,
                page_w - 2*x_margin
            )
            ts_mnl = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d %H:%M")
            y = _draw_paragraph(
                c,
                f"Generated: {ts_mnl}",
                subtitle_style,
                x_margin,
                y,
                page_w - 2*x_margin
            )
            y -= 6 * mm

            # Redraw logo on each new page
            if logo_path and os.path.isfile(logo_path):
                try:
                    img = Image(logo_path)
                    img._restrictSize(42*mm, 18*mm)
                    img_w, img_h = img.drawWidth, img.drawHeight
                    img_x = page_w - x_margin - img_w
                    img_y = page_h - y_margin - img_h
                    img.drawOn(c, img_x, img_y)
                except Exception:
                    pass

        page_data = [data[0]] + chunk
        page_table = Table(page_data, colWidths=col_widths)

        page_table.setStyle(TableStyle([
            ("FONT", (0,0), (-1,0), "Helvetica-Bold", 9),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#233b64")),

            # Deep red ONLY for last header cell
            ("BACKGROUND", (-1,0), (-1,0), colors.HexColor("#8B0000")),
            ("TEXTCOLOR", (-1,0), (-1,0), colors.white),

            ("ALIGN", (2,1), (2,-1), "RIGHT"),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#d8e2f0")),
            ("ROWBACKGROUNDS", (0,1), (-2,-1), [colors.whitesmoke, colors.HexColor("#f7f9fc")]),

            # Light red signature cells
            ("BACKGROUND", (-1,1), (-1,-1), colors.HexColor("#FDECEC")),

            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),

            # Taller voucher rows — designed for about 6 vouchers per page
            ("TOPPADDING", (0,1), (-1,-1), 22),
            ("BOTTOMPADDING", (0,1), (-1,-1), 22),

            # Header
            ("TOPPADDING", (0,0), (-1,0), 7),
            ("BOTTOMPADDING", (0,0), (-1,0), 7),
        ]))

        tw, th = page_table.wrapOn(c, page_w - 2*x_margin, y - 10*mm)
        page_table.drawOn(c, x_margin, y - th)
        y = y - th - (8 * mm)

    # FAQ section
    def ensure_space(h_needed):
        nonlocal y
        if y - h_needed < 18 * mm:
            c.showPage()
            y = page_h - y_margin

    faq_blocks = [
        ("Frequently Asked Questions", faq_heading),

        ("Q: How do I redeem a voucher? Paano mag-redeem ng voucher?", faq_q),
        ("Verify the driver and license details, pump the fuel, sign the PDF, then send a photo of the signed PDF to the UniFleet team ASAP on Viber.\n"
         "Siguraduhing tama ang mga detalye ng lisensya at ng driver, kargahan ang sasakyan, pirmahan ang PDF, picturan ang nakapirmang PDF at ipadala kaagad sa Viber sa UniFleet team.", faq_a),

        ("Q: What if a driver goes to the wrong station? Paano kapag pumunta ang customer sa maling istasyon o branch?", faq_q),
        ("If within the same station network, a voucher can still be redeemed as long as the driver and vehicle details match.\n"
         "Maari pa ring kargahan ang sasakyan kapag ang voucher ay para sa tamang network (halimbawa: EcoOil voucher, maaaring gamitin sa ibang EcoOil station).", faq_a),

        ("Q: Who do I contact if there’s an issue? Paano kapag may natatanggap na problema?", faq_q),
        ("Station staff should contact their station manager. Station managers should contact UniFleet via the Viber group chat.\n"
         "Kapag ikaw ay station staff, ipaalam ang problema sa station manager. Ang station manager ang makikipag-usap sa UniFleet team gamit ang Viber.", faq_a),
    ]

    max_width = page_w - 2 * x_margin
    for text, style in faq_blocks:
        p = Paragraph(text, style)
        w, h_est = p.wrap(max_width, 1000)
        ensure_space(h_est + 2*mm)
        y = _draw_paragraph(c, text, style, x_margin, y, max_width)

    c.showPage()
    c.save()
    return buf.getvalue()
