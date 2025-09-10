import os
import time  # <<< ADDED
from datetime import datetime
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont

import price_store  # <<< ADDED: read live prices from station_prices.json

# File paths
MASTER_VOUCHERS = 'data/master_vouchers.csv'
QR_OUTPUT_DIR = 'static/qr_codes/'
LOGO_PATH = 'static/UniFleet Logo.png'  # ✅ Branded logo
TEMPLATE_PATH = 'static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png'  # ✅ Branded PNG template
REQUIRED_COLUMNS = [
    'voucher_id', 'station', 'requested_amount_php', 'liters_requested',
    'transaction_date', 'expected_refill_date', 'live_price_php_per_liter',
    'discount_per_liter', 'discount_total', 'total_dispensed', 'liters_dispensed',
    'driver_name', 'vehicle_plate', 'truck_make', 'truck_model',
    'number_of_wheels', 'status', 'redemption_timestamp'
]

BASE_URL = "https://c62ded05-595f-42d6-b59c-55cd5cb986e6-00-287s4ts5huint.sisko.replit.dev"

os.makedirs(QR_OUTPUT_DIR, exist_ok=True)

def _norm(s):
    return str(s or "").strip().lower()

def _resolve_live_price(station_field):
    """
    Resolve the station price from station_prices.json by:
    1) id match (exact)
    2) name match (case-insensitive)
    Returns dict with price + updated_at (no freshness gating).
    """
    stations = price_store.list_stations()
    sf = _norm(station_field)

    # id match
    for s in stations:
        if _norm(s.get("id")) == sf:
            return {
                "price": s.get("price_php_per_liter"),
                "updated_at": int(s.get("updated_at", 0) or 0),
                "station_id": s.get("id"),
                "station_name": s.get("name")
            }

    # name match
    for s in stations:
        if _norm(s.get("name")) == sf:
            return {
                "price": s.get("price_php_per_liter"),
                "updated_at": int(s.get("updated_at", 0) or 0),
                "station_id": s.get("id"),
                "station_name": s.get("name")
            }

    return {"price": None, "updated_at": 0, "station_id": None, "station_name": None}

def generate_qr_image(voucher_data, row_index):
    voucher_id = str(voucher_data['voucher_id']).strip()
    plate = str(voucher_data.get('vehicle_plate', '')).strip()

    qr_content = f"{BASE_URL}/redeem/{voucher_id}"
    qr = qrcode.make(qr_content)
    qr_img = qr.convert("RGB")

    final_img = Image.new("RGB", (qr_img.width, qr_img.height + 90), "white")
    final_img.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(final_img)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except:
        font = ImageFont.load_default()

    draw.text((10, qr_img.height + 10), f"{voucher_id} | {plate}", fill="black", font=font)

    filename = f"{voucher_id}.png"
    filepath = os.path.join(QR_OUTPUT_DIR, filename)
    final_img.save(filepath)
    print(f"✅ Saved QR voucher: {filepath}")

def generate_branded_image(voucher_data):
    voucher_id = str(voucher_data['voucher_id']).strip()
    qr_path = os.path.join(QR_OUTPUT_DIR, f"{voucher_id}.png")

    if not os.path.exists(qr_path):
        print(f"⚠️ QR not found for {voucher_id}. Skipping branded image.")
        return

    template_path = 'static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png'
    if not os.path.exists(template_path):
        print(f"⚠️ Template not found: {template_path}")
        return

    try:
        base = Image.open(template_path).convert("RGB")
        qr = Image.open(qr_path).resize((750, 750))  # ✅ Larger QR

        draw = ImageDraw.Draw(base)
        try:
            font_label = ImageFont.truetype("static/Roboto-Bold.ttf", 42)
            font_value = ImageFont.truetype("static/Roboto-Regular.ttf", 42)
        except:
            print("⚠️ Failed to load Roboto fonts. Using default.")
            font_label = font_value = ImageFont.load_default()

        # Paste QR – slightly lower to center visually
        qr_x = (base.width - qr.width) // 2
        qr_y = 525  # ✅ Moved down
        base.paste(qr, (qr_x, qr_y))

        # Text content
        y = qr_y + qr.height + 70
        left_margin = 90
        spacing = 70  # Pixel distance between lines

        entries = [
            ("PHP Value:", f"₱{voucher_data.get('total_dispensed', '')} (Includes ₱{voucher_data.get('requested_amount_php', '')} Prepaid + ₱{voucher_data.get('discount_total', '')} FREE)"),
            ("Driver Name:", voucher_data.get('driver_name', '')),
            ("Plate:", voucher_data.get('vehicle_plate', '')),
            ("Station:", voucher_data.get('station', '')),
            ("Valid Date:", voucher_data.get('expected_refill_date', '')),
            ("Voucher ID:", voucher_id)
        ]

        for label, value in entries:
            draw.text((left_margin, y), label, fill="black", font=font_label)
            label_width = draw.textlength(label, font=font_label)
            draw.text((left_margin + label_width + 20, y), value, fill="black", font=font_value)
            y += spacing

        output_path = os.path.join(QR_OUTPUT_DIR, f"{voucher_id}_Official.png")
        base.save(output_path)
        print(f"🏷️ Branded PNG saved: {output_path}")

    except Exception as e:
        print(f"❌ Failed to generate branded image for {voucher_id}: {e}")

def append_and_generate_vouchers(csv_path):
    df = pd.read_csv(csv_path, encoding='utf-8-sig')

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in uploaded CSV: {missing}")

    df = df[REQUIRED_COLUMNS]
    df['voucher_id'] = df['voucher_id'].astype(str)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    for idx, row in df.iterrows():
        voucher_id = str(row['voucher_id']).strip().lower()
        if voucher_id == 'nan' or voucher_id == '':
            df.at[idx, 'voucher_id'] = f"UF{timestamp}{idx:02d}"

        # Resolve the live price from station_prices.json (always use it if present > 0)
        station_field = row.get('station', '')
        lp = _resolve_live_price(station_field)

        # Decide which price to use:
        # 1) JSON price if present and > 0
        # 2) fall back to CSV's live_price_php_per_liter (if provided)
        calc_price = None
        try:
            if lp['price'] is not None and float(lp['price']) > 0:
                calc_price = float(lp['price'])
                # write back for reproducibility so row is self-contained
                df.at[idx, 'live_price_php_per_liter'] = round(calc_price, 2)
            else:
                calc_price = float(row['live_price_php_per_liter'])
        except Exception:
            calc_price = None

        # Fill missing calculations using calc_price (if available)
        if calc_price is None or calc_price <= 0:
            print(f"⚠️ No usable price for station '{station_field}' on row {idx}; skipping auto-calcs.")
        else:
            try:
                if pd.isna(row['liters_requested']) or str(row['liters_requested']).strip() == '':
                    req_amt = float(row['requested_amount_php'])
                    df.at[idx, 'liters_requested'] = round(req_amt / calc_price, 2)
            except Exception:
                pass

            try:
                if pd.isna(row['discount_total']) or str(row['discount_total']).strip() == '':
                    liters = float(df.at[idx, 'liters_requested'])
                    discount = float(row['discount_per_liter'])
                    df.at[idx, 'discount_total'] = round(liters * discount, 2)
            except Exception:
                pass

            try:
                if pd.isna(row['total_dispensed']) or str(row['total_dispensed']).strip() == '':
                    total_dispensed = float(df.at[idx, 'requested_amount_php']) + float(df.at[idx, 'discount_total'])
                    df.at[idx, 'total_dispensed'] = round(total_dispensed, 2)
            except Exception:
                pass

            try:
                if pd.isna(row['liters_dispensed']) or str(row['liters_dispensed']).strip() == '':
                    liters_dispensed = float(df.at[idx, 'liters_requested']) + (
                        float(df.at[idx, 'discount_total']) / calc_price
                    )
                    df.at[idx, 'liters_dispensed'] = round(liters_dispensed, 2)
            except Exception:
                pass

        # Keep your existing default exactly as-is
        if pd.isna(row['status']) or str(row['status']).strip() == '':
            df.at[idx, 'status'] = 'unredeemed'

    if os.path.exists(MASTER_VOUCHERS):
        existing_df = pd.read_csv(MASTER_VOUCHERS, encoding='utf-8-sig')
        updated_df = pd.concat([existing_df, df], ignore_index=True)
        updated_df.to_csv(MASTER_VOUCHERS, index=False, encoding='utf-8-sig')
    else:
        df.to_csv(MASTER_VOUCHERS, index=False, encoding='utf-8-sig')

    print(f"📦 Appended {len(df)} rows to master_vouchers.csv")

    for idx, row in df.iterrows():
        generate_qr_image(row, idx)
        generate_branded_image(row)  # 🆕

    try:
        os.remove(csv_path)
        print(f"🩹 Removed temporary upload file: {csv_path}")
    except Exception as e:
        print(f"⚠️ Could not remove {csv_path}: {e}")

if __name__ == "__main__":
    upload_path = "data/unifleet_web_redemptions_input.csv"
    append_and_generate_vouchers(upload_path)
