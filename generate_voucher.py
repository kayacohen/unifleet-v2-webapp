import os
import time
from datetime import datetime
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont

import price_store  # read live prices from station_prices.json
from persistence import get_repo  # repo abstraction (CSV or DB)

# F2.6: paths come from data_paths so QR PNGs and template/logo
# assets are resolved against the volume + the image-baked static dir.
import data_paths
data_paths.ensure_dirs()

# File paths
MASTER_VOUCHERS = str(data_paths.LEGACY_MASTER_VOUCHERS_CSV)  # CSV-mode back-compat
QR_OUTPUT_DIR = str(data_paths.QR_DIR) + "/"
LOGO_PATH = data_paths.STATIC_LOGO_PATH
TEMPLATE_PATH = data_paths.STATIC_VOUCHER_TEMPLATE_PATH
REQUIRED_COLUMNS = [
    'voucher_id', 'station', 'requested_amount_php', 'liters_requested',
    'transaction_date', 'expected_refill_date', 'live_price_php_per_liter',
    'discount_per_liter', 'discount_total', 'total_dispensed', 'liters_dispensed',
    'driver_name', 'vehicle_plate', 'truck_make', 'truck_model',
    'number_of_wheels', 'status', 'redemption_timestamp'
]

# NOTE: Read BASE_URL from env if provided; fall back to prior host.
# Keep same variable name and behavior for compatibility.
BASE_URL = os.environ.get(
    "BASE_URL",
    "https://c62ded05-595f-42d6-b59c-55cd5cb986e6-00-287s4ts5huint.sisko.replit.dev"
).strip().rstrip("/")

os.makedirs(QR_OUTPUT_DIR, exist_ok=True)  # F2.6: ensure_dirs() above already created it

# Persistence selector
PERSISTENCE_BACKEND = os.environ.get("PERSISTENCE_BACKEND", "csv").lower()
_gen_repo = get_repo(PERSISTENCE_BACKEND)


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

    for s in stations:
        if _norm(s.get("id")) == sf:
            return {
                "price": s.get("price_php_per_liter"),
                "updated_at": int(s.get("updated_at", 0) or 0),
                "station_id": s.get("id"),
                "station_name": s.get("name")
            }

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

    template_path = TEMPLATE_PATH
    if not os.path.exists(template_path):
        print(f"⚠️ Template not found: {template_path}")
        return

    def _fmt(v):
        """Render a value as a drawable string.

        PG-backed rows may have datetime objects for date columns; CSV-backed
        rows may have str/None. Normalize to a str so Pillow's draw.text
        (which iterates the str) doesn't blow up on a datetime.
        """
        if v is None:
            return ''
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d")
        return str(v)

    try:
        base = Image.open(template_path).convert("RGB")
        qr = Image.open(qr_path).resize((750, 750))

        draw = ImageDraw.Draw(base)
        try:
            font_label = ImageFont.truetype("static/Roboto-Bold.ttf", 42)
            font_value = ImageFont.truetype("static/Roboto-Regular.ttf", 42)
        except:
            print("⚠️ Failed to load Roboto fonts. Using default.")
            font_label = font_value = ImageFont.load_default()

        qr_x = (base.width - qr.width) // 2
        qr_y = 525
        base.paste(qr, (qr_x, qr_y))

        y = qr_y + qr.height + 70
        left_margin = 90
        spacing = 70

        entries = [
            ("PHP Value:", f"₱{_fmt(voucher_data.get('total_dispensed'))} (Includes ₱{_fmt(voucher_data.get('requested_amount_php'))} Prepaid + ₱{_fmt(voucher_data.get('discount_total'))} FREE)"),
            ("Driver Name:", _fmt(voucher_data.get('driver_name'))),
            ("Plate:", _fmt(voucher_data.get('vehicle_plate'))),
            ("Station:", _fmt(voucher_data.get('station'))),
            ("Valid Date:", _fmt(voucher_data.get('expected_refill_date'))),
            ("Voucher ID:", voucher_id)
        ]

        for label, value in entries:
            draw.text((left_margin, y), label, fill="black", font=font_label)
            try:
                label_width = draw.textlength(label, font=font_label)
            except Exception:
                # Fallback for older Pillow versions
                label_width = draw.textbbox((0, 0), label, font=font_label)[2]
            draw.text((left_margin + label_width + 20, y), value, fill="black", font=font_value)
            y += spacing

        output_path = os.path.join(QR_OUTPUT_DIR, f"{voucher_id}_Official.png")
        base.save(output_path)
        print(f"🏷️ Branded PNG saved: {output_path}")

    except Exception as e:
        print(f"❌ Failed to generate branded image for {voucher_id}: {e}")


def append_and_generate_vouchers(csv_path):
    """
    Request-time behavior:
      - Parse and normalize uploaded CSV
      - Compute any missing numeric fields using live price when possible
      - Default status to 'Unverified'
      - Append rows via repo (CSV or DB depending on env)
      - DO NOT generate any QR/PNG here
    """
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

        station_field = row.get('station', '')
        lp = _resolve_live_price(station_field)

        calc_price = None
        try:
            if lp['price'] is not None and float(lp['price']) > 0:
                calc_price = float(lp['price'])
                df.at[idx, 'live_price_php_per_liter'] = round(calc_price, 2)
            else:
                calc_price = float(row['live_price_php_per_liter'])
        except Exception:
            calc_price = None

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

        if pd.isna(row['status']) or str(row['status']).strip() == '':
            df.at[idx, 'status'] = 'Unverified'  # approval-gated flow

        if pd.isna(row['redemption_timestamp']) or str(row['redemption_timestamp']).strip() == '':
            df.at[idx, 'redemption_timestamp'] = ''

    rows_to_add = df.to_dict(orient='records')
    _gen_repo.append_vouchers(rows_to_add)
    print(f"📦 Appended {len(rows_to_add)} rows to {'database' if PERSISTENCE_BACKEND=='db' else 'master_vouchers.csv'} as Unverified")

    try:
        os.remove(csv_path)
        print(f"🩹 Removed temporary upload file: {csv_path}")
    except Exception as e:
        print(f"⚠️ Could not remove {csv_path}: {e}")

    # IMPORTANT: no QR/PNG generation here (approval will generate assets)
    return


# ===== Approval-time asset generation (used by ops approval path) =====
def generate_assets_for_row(row: dict) -> None:
    """
    Idempotent: creates QR (/redeem/<voucher_id>) and branded PNG if missing.
    Does NOT write back to DB/CSV. Safe to call multiple times.
    """
    vid = str(row.get("voucher_id", "")).strip()
    if not vid:
        raise ValueError("Missing voucher_id for asset generation")

    qr_file = os.path.join(QR_OUTPUT_DIR, f"{vid}.png")
    official_png = os.path.join(QR_OUTPUT_DIR, f"{vid}_Official.png")

    if not os.path.exists(qr_file):
        try:
            generate_qr_image(row, 0)
        except Exception as e:
            raise RuntimeError(f"QR generation failed for {vid}: {e}")

    if not os.path.exists(official_png):
        try:
            generate_branded_image(row)
        except Exception as e:
            raise RuntimeError(f"Branded PNG generation failed for {vid}: {e}")


if __name__ == "__main__":
    upload_path = str(data_paths.UPLOADED_REDEMPTIONS_CSV)
    append_and_generate_vouchers(upload_path)
