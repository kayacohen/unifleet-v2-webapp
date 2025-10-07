from flask import (
    Flask, render_template, request, redirect, send_file, abort,
    url_for, flash, jsonify, make_response
)
import os
import io
import subprocess
import pandas as pd
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import random
import string
import csv
import re
import pytz

import price_store
from persistence import get_repo  # repo abstraction (CSV or DB)
from generate_voucher import generate_assets_for_row  # approval-time asset generation
# PDF builder is optional; don't crash app if it's missing
try:
    from report_pdf import build_supplier_pdf  # supplier PDF builder
    _PDF_IMPORT_ERROR = None
except Exception as _e:
    build_supplier_pdf = None
    _PDF_IMPORT_ERROR = str(_e)

# NEW: discounts storage
from discount_store import DiscountStore, DiscountValueError

app = Flask(__name__)

# =========================
# Filters / Utilities
# =========================

# --- Lightweight health probe (for UptimeRobot/AppScript warmups) ---
@app.route("/healthz", methods=["GET", "HEAD"])
def healthz():
    # Flask will normally treat HEAD like GET and then strip the body,
    # but Replit proxies sometimes glitch. This avoids issues.
    if request.method == "HEAD":
        return ("", 200, {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store"
        })
    return ("ok", 200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store"
    })


@app.template_filter("manila_time")
def manila_time_filter(value):
    """
    Render a date/time value as Asia/Manila local time in 'YYYY-MM-DD HH:MM'.
    Rules:
      - If an ISO string has NO timezone (naive), treat it as Manila local.
      - If an ISO string HAS a timezone/offset, convert to Manila.
      - If it's a legacy short date like '7/19/25', show as-is.
    """
    if not value:
        return "—"

    s = str(value).strip()

    # Legacy short dates like '7/19/25' -> don't try to convert
    if "/" in s and len(s) <= 10:
        return s

    manila = pytz.timezone("Asia/Manila")

    # Try ISO first
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = manila.localize(dt)
        else:
            dt = dt.astimezone(manila)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    # Try generic parsing as last resort
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(s, fmt)
            dt = manila.localize(dt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue

    return s

app.secret_key = 'your_secret_key_here'  # Required for flashing messages

SUPPLIER_API_TOKEN = os.environ.get("SUPPLIER_API_TOKEN", "unifleet2025mvp")  # Default token
ADMIN_KEY = os.environ.get("ADMIN_KEY", "unifleet-admin")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs("data/presets", exist_ok=True)

# Initialize price store JSON on startup (creates data/station_prices.json if missing)
price_store.init_if_missing()

# Persistence backend: 'csv' (default) or 'db'
PERSISTENCE_BACKEND = os.environ.get("PERSISTENCE_BACKEND", "csv").lower()
repo = get_repo(PERSISTENCE_BACKEND)

# ===== Runtime flags / tokens (optional) =====
ENFORCE_PHASES = os.environ.get("ENFORCE_PHASES", "").strip() == "1"
OPS_TOKEN = os.environ.get("OPS_TOKEN", "").strip()

# ===== Payment instructions config =====
PAYMENT_INFO = {
    "gotyme": {
        "label": "GoTyme Bank",
        "account_name": "UniFleet Inc.",
        "account_number": "1234-5678-9012",  # <-- replace with real
    },
    "fee_note": "Bank/app transfer fees are paid by you/sender. Your voucher will not be activated until payment is confirmed. Send payment confirmation to 0945-149-2369."
}


# ===== Tiny CSV-safe audit log =====
AUDIT_PATH = "data/ops_audit_log.csv"
AUDIT_FIELDS = [
    "timestamp", "action", "voucher_id",
    "from_status", "to_status",
    "route", "actor_ip", "user_agent", "note"
]

def append_audit(action, voucher_id, from_status="", to_status="", note=""):
    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    is_new = not os.path.isfile(AUDIT_PATH)
    try:
        with open(AUDIT_PATH, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "voucher_id": voucher_id,
                "from_status": from_status or "",
                "to_status": to_status or "",
                "route": request.path,
                "actor_ip": request.headers.get("X-Forwarded-For", request.remote_addr),
                "user_agent": request.headers.get("User-Agent", ""),
                "note": note or ""
            })
    except Exception as e:
        print(f"⚠️ Audit log write failed: {e}")

# ===== Price change history (CSV audit) =====
PRICE_HISTORY_PATH = "data/price_history.csv"
PRICE_HISTORY_FIELDS = [
    "timestamp_iso", "timestamp_unix", "station_id",
    "old_price", "new_price", "actor_ip", "user_agent"
]

def append_price_history(station_id, old_price, new_price, updated_unix):
    """Append a price change row; timestamp_iso is logged in Asia/Manila local time."""
    os.makedirs(os.path.dirname(PRICE_HISTORY_PATH), exist_ok=True)
    is_new = not os.path.isfile(PRICE_HISTORY_PATH)
    try:
        with open(PRICE_HISTORY_PATH, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=PRICE_HISTORY_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({
                "timestamp_iso": datetime.fromtimestamp(int(updated_unix), tz=ZoneInfo("Asia/Manila")).isoformat(timespec="seconds"),
                "timestamp_unix": int(updated_unix),
                "station_id": station_id,
                "old_price": old_price if old_price is not None else "",
                "new_price": new_price,
                "actor_ip": request.headers.get("X-Forwarded-For", request.remote_addr),
                "user_agent": request.headers.get("User-Agent", ""),
            })
    except Exception as e:
        print(f"⚠️ Price history write failed: {e}")

def _ensure_voucher_columns(df: pd.DataFrame) -> pd.DataFrame:
    if 'status' not in df.columns:
        df['status'] = ""
    if 'redemption_timestamp' not in df.columns:
        df['redemption_timestamp'] = ""
    return df

# ---------- Helpers for parity/export ----------
def _coalesce(*vals):
    """Return the first non-empty/non-NaN value."""
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            continue
        return v
    return None

def _fmt_money(v):
    """Format numeric-like value to 2 decimals, empty if not a number."""
    try:
        return f"{float(v):.2f}"
    except Exception:
        return ""

def _check_admin_key(req):
    key = req.args.get("key") or req.headers.get("X-Admin-Key")
    return key == ADMIN_KEY

# =========================
# Home / Dashboard
# =========================
@app.route('/')
def home():
    return redirect("/form")

@app.route('/form')
def form():
    # existing voucher table data
    try:
        vouchers = repo.list_recent_vouchers(limit=50)
        for row in vouchers:
            vid = str(row.get("voucher_id", "")).strip()
            png_1 = os.path.exists(f"static/qr_codes/{vid}.png")
            png_2 = os.path.exists(f"static/qr_codes/{vid}_Official.png")
            row['png_exists'] = png_1 and png_2
    except Exception as e:
        print(f"⚠️ Error loading vouchers: {e}")
        vouchers = []

    # NEW: supply station options + persisted selections for the PDF filter UI
    stations = price_store.list_stations()  # [{id, name, brand, ...}]
    stations = sorted(stations, key=lambda s: (s.get("brand",""), s.get("name","")))
    cookie_val = request.cookies.get("pdf_station_ids", "")
    # Accept either comma or pipe as delimiter
    cookie_val_norm = cookie_val.replace("|", ",")
    selected_station_ids = [s.strip() for s in cookie_val_norm.split(",") if s.strip()]


    return render_template(
        "form.html",
        today=date.today().isoformat(),
        vouchers=vouchers,
        ops_token=OPS_TOKEN,
        station_options=stations,
        selected_station_ids=selected_station_ids,
    )

# -------------- (CSV upload route stays; you’ll remove visually in form.html soon) --------------
@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    uploaded_file = request.files['csv_file']
    if uploaded_file.filename != '':
        filepath = os.path.join("data", "unifleet_web_redemptions_input.csv")
        uploaded_file.save(filepath)
        result = subprocess.run(["python3", "generate_voucher.py"], capture_output=True, text=True)
        print(result.stdout)
        print(result.stderr)
    return redirect("/form")

@app.route('/delete_png/<voucher_id>', methods=['POST'])
def delete_png(voucher_id):
    try:
        for path in [f"static/qr_codes/{voucher_id}.png", f"static/qr_codes/{voucher_id}_Official.png"]:
            if os.path.exists(path):
                os.remove(path)
        return redirect(url_for('form'))
    except Exception as e:
        print(f"❌ Error deleting PNGs for {voucher_id}: {e}")
        return f"<h2>Error deleting PNGs for {voucher_id}: {str(e)}</h2>", 500

@app.route('/redeem/<voucher_id>', methods=['GET'])
def redeem_page(voucher_id):
    row = repo.get_voucher(voucher_id)
    if not row:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    return render_template('redeem.html', voucher=row)

@app.route('/redeem/<voucher_id>', methods=['POST'])
def mark_redeemed(voucher_id):
    row = repo.get_voucher(voucher_id)
    if not row:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    current_status = str(row.get('status', '')).strip()
    allowed = (current_status in ('', 'Unverified', 'Unredeemed'))
    if ENFORCE_PHASES:
        allowed = (current_status == 'Unredeemed')
    if not allowed:
        append_audit("redeem_denied", voucher_id, current_status, "Redeemed", f"enforce_phases={int(ENFORCE_PHASES)}")
        return f"<h2>Cannot redeem voucher while status is '{current_status or 'Unverified'}'.</h2>", 400
    ts = datetime.now().isoformat(timespec='seconds')
    repo.set_status(voucher_id, 'Redeemed', ts)
    append_audit("redeem_success", voucher_id, current_status, "Redeemed", f"enforce_phases={int(ENFORCE_PHASES)}")
    return redirect(f"/redeem/{voucher_id}")

@app.route('/ops/voucher/<voucher_id>/status/<new_status>', methods=['GET'])
def ops_set_status(voucher_id, new_status):
    if OPS_TOKEN and request.args.get("token", "") != OPS_TOKEN:
        return "<h2>Forbidden: invalid token.</h2>", 403
    allowed_targets = {'Unverified', 'Unredeemed', 'Redeemed'}
    if new_status not in allowed_targets:
        return f"<h2>Invalid status '{new_status}'.</h2>", 400
    row = repo.get_voucher(voucher_id)
    if not row:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    prev = str(row.get('status','')).strip()

    if new_status == 'Redeemed':
        ts = datetime.now().isoformat(timespec='seconds')
        repo.set_status(voucher_id, 'Redeemed', ts)

    elif new_status == 'Unredeemed':
        # === Approve flow: compute & persist first, then (re)generate assets ===
        from datetime import datetime as _dt

        station_name = (row.get("station") or "").strip()
        try:
            amount = float(row.get("requested_amount_php") or 0)
        except Exception:
            amount = 0.0

        # Prefer booking-time snapshots
        try:
            snap_price = float(row.get("price_snapshot_php_per_liter") or 0)
        except Exception:
            snap_price = 0.0
        try:
            snap_disc = float(row.get("discount_snapshot_php_per_liter") or 0)
        except Exception:
            snap_disc = 0.0

        price_updated_at = int(row.get("price_snapshot_updated_at") or 0) if str(row.get("price_snapshot_updated_at") or "").isdigit() else 0
        disc_captured_at = int(row.get("discount_snapshot_captured_at") or 0) if str(row.get("discount_snapshot_captured_at") or "").isdigit() else 0

        # Fallbacks to current live values if snapshot missing/zero
        price = snap_price
        if price <= 0:
            match = None
            for s in price_store.list_stations():
                if (s.get("name") or "").strip().lower() == station_name.lower():
                    match = s
                    break
            price = float(match.get("price_php_per_liter") or 0) if match else 0.0
            price_updated_at = int(match.get("updated_at") or 0) if match else 0

        dpl = snap_disc
        if dpl < 0:
            dpl = 0.0
        if dpl == 0.0:
            try:
                dpl_live = discount_store.get(station_name)
                if dpl_live is not None:
                    dpl = float(dpl_live)
            except Exception:
                pass
            if not disc_captured_at:
                disc_captured_at = int(_dt.now().timestamp())

        # ---- Do the math (guard against zero price) ----
        if amount > 0 and price > 0:
            liters_requested = round(amount / price, 2)
            discount_total = round(liters_requested * dpl, 2)
            total_dispensed = round(amount + discount_total, 2)
            liters_dispensed = round(liters_requested + (discount_total / price if price else 0), 2)
        else:
            liters_requested = 0.0
            discount_total = 0.0
            total_dispensed = amount
            liters_dispensed = 0.0

        # ---- Persist (repo.update_voucher_fields mirrors *_php to legacy columns) ----
        repo.update_voucher_fields(voucher_id, {
            # store the values we actually used
            "live_price_php_per_liter": price,
            "discount_per_liter": dpl,

            # keep snapshots if they weren't already stored
            "price_snapshot_php_per_liter": row.get("price_snapshot_php_per_liter") or price,
            "price_snapshot_updated_at": row.get("price_snapshot_updated_at") or price_updated_at,
            "discount_snapshot_php_per_liter": row.get("discount_snapshot_php_per_liter") or dpl,
            "discount_snapshot_captured_at": row.get("discount_snapshot_captured_at") or disc_captured_at,

            # computed totals
            "liters_requested": liters_requested,
            "discount_total_php": discount_total,
            "total_dispensed_php": total_dispensed,
            "liters_dispensed": liters_dispensed,

            # bookkeeping
            "computed_at": _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        })

        # ---- Reload fresh row & (re)generate assets *after* fields exist ----
        try:
            fresh = repo.get_voucher(voucher_id)
            # If numbers are still missing, fail fast
            if not fresh or not fresh.get("requested_amount_php") or price <= 0:
                append_audit("ops_generate_assets_skip", voucher_id, prev, new_status, "missing amount/price after compute")
                return "<h2>Cannot generate assets: missing amount/price after compute.</h2>", 400

            generate_assets_for_row(fresh)
        except Exception as gen_err:
            append_audit("ops_generate_assets_error", voucher_id, prev, new_status, str(gen_err))
            return f"<h2>Failed to generate voucher assets: {gen_err}</h2>", 500

        # finally flip status to Unredeemed
        repo.set_status(voucher_id, 'Unredeemed', "")

    else:
        repo.set_status(voucher_id, new_status, "")

    append_audit("ops_set_status", voucher_id, prev, new_status, f"token_ok={int(bool(not OPS_TOKEN or request.args.get('token','')==OPS_TOKEN))}")

    # Redirect back to caller, defaulting to /form
    next_url = request.args.get("next") or request.referrer or url_for("form")
    return redirect(next_url)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        company_name = request.form.get('company_name', '').strip()
        clean = re.sub(r'[^A-Za-z]', '', company_name.upper())
        account_code = (clean[:4] if len(clean) >= 4 else ''.join(random.choices(string.ascii_uppercase, k=4)))
        def sanitize(v): return str(v).strip() if v else ''
        new_row = {
            'account_code': account_code,
            'contact_name': sanitize(request.form.get('contact_name')),
            'contact_number': sanitize(request.form.get('contact_number')),
            'email': sanitize(request.form.get('email')),
            'company_name': sanitize(company_name),
            'fleet_size': sanitize(request.form.get('fleet_size')),
            'areas': sanitize(request.form.get('areas')),
            'refuel_locations': sanitize(request.form.get('refuel_locations')),
            'hq_locations': sanitize(request.form.get('hq_locations'))
        }
        customers_path = 'data/customers.csv'
        if os.path.isfile(customers_path):
            df = pd.read_csv(customers_path, dtype=str)
        else:
            df = pd.DataFrame(columns=list(new_row.keys()))
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(customers_path, index=False, encoding='utf-8-sig')
        return redirect(f"/register/success?account_code={account_code}")
    return render_template('register.html')

@app.route('/register/success')
def register_success():
    return render_template('register_success.html', account_code=request.args.get('account_code'))

@app.route('/test_success')
def test_success():
    return render_template('register_success.html', account_code="TEST")

@app.route('/book', methods=['GET', 'POST'])
def book():
    customers_path = 'data/customers.csv'
    booking_path = 'data/requested_vouchers.csv'
    try:
        # Pull from live price store so new stations auto-appear
        station_objs = price_store.list_stations()  # [{id, name, brand, ...}]
        station_names = [s.get("name", "") for s in station_objs]
        # Case-insensitive alphabetical sort
        station_names = sorted([n for n in station_names if n], key=lambda x: x.lower())
    except Exception as e:
        print(f"⚠️ Error loading stations: {e}")
        station_names = []


    # Compute Manila "now + 24h" for form hint and validation baseline
    manila = ZoneInfo("Asia/Manila")
    min_refuel_dt = (datetime.now(manila) + timedelta(hours=24))
    min_refuel = min_refuel_dt.strftime("%Y-%m-%dT%H:%M")

    if request.method == 'POST':
        account_code = request.form.get('account_code', '').strip().upper()
        try:
            df = pd.read_csv(customers_path, encoding='utf-8')
        except pd.errors.ParserError:
            return "<h2>Error: 'customers.csv' is malformed.</h2>", 500
        df.columns = df.columns.str.replace('\ufeff', '').str.strip().str.lower()
        df['account_code'] = df['account_code'].astype(str).str.strip().str.upper()
        rows = df[df['account_code'] == account_code]

        if not request.form.get('station'):
            if rows.empty:
                return render_template('book.html', customer=None, presets=[], station_names=station_names, min_refuel=min_refuel)
            base = rows.iloc[0].to_dict()
            preset_path = f"data/presets/{account_code}_presets.csv"
            presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
            return render_template('book.html', customer=base, presets=presets, station_names=station_names, min_refuel=min_refuel)

        driver_mode = request.form.get('driver_mode')
        use_new = driver_mode == 'new'
        if driver_mode == 'preset' and not request.form.get('driver_select'):
            flash("Please select a preset or switch to 'Add New Driver'", "error")
            base = rows.iloc[0].to_dict()
            preset_path = f"data/presets/{account_code}_presets.csv"
            presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
            return render_template('book.html', customer=base, presets=presets, station_names=station_names, form_values=request.form, min_refuel=min_refuel)

        if use_new:
            driver_data = {
                'driver_name': request.form.get('driver_name'),
                'vehicle_plate': request.form.get('vehicle_plate'),
                'truck_make': request.form.get('truck_make'),
                'truck_model': request.form.get('truck_model'),
                'number_of_wheels': request.form.get('number_of_wheels'),
                'fuel_type': request.form.get('fuel_type')
            }
        else:
            parts = request.form.get('driver_select').split('|')
            driver_data = {
                'driver_name': parts[0],
                'vehicle_plate': parts[1],
                'truck_make': parts[2],
                'truck_model': parts[3],
                'number_of_wheels': parts[4],
                'fuel_type': parts[5]
            }

        # === NEW: Validate refuel_datetime >= now+24h (Asia/Manila) ===
        refuel_dt_str = (request.form.get('refuel_datetime') or '').strip()
        try:
            # HTML datetime-local is naive; interpret as Manila local
            refuel_dt_mnl = datetime.strptime(refuel_dt_str, "%Y-%m-%dT%H:%M").replace(tzinfo=manila)
            if refuel_dt_mnl < min_refuel_dt:
                flash("Refuel Date & Time must be at least 24 hours from now (Asia/Manila).", "error")
                base = rows.iloc[0].to_dict() if not rows.empty else None
                preset_path = f"data/presets/{account_code}_presets.csv"
                presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
                return render_template('book.html',
                                       customer=base,
                                       presets=presets,
                                       station_names=station_names,
                                       form_values=request.form,
                                       min_refuel=min_refuel)
        except Exception:
            # If parsing fails, treat as invalid
            flash("Please enter a valid Refuel Date & Time (YYYY-MM-DDTHH:MM).", "error")
            base = rows.iloc[0].to_dict() if not rows.empty else None
            preset_path = f"data/presets/{account_code}_presets.csv"
            presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
            return render_template('book.html',
                                   customer=base,
                                   presets=presets,
                                   station_names=station_names,
                                   form_values=request.form,
                                   min_refuel=min_refuel)

        # ---- CAPTURE BOOKING-TIME SNAPSHOTS (price & discount) ----
        station_name = (request.form.get('station') or '').strip()

        # Robust normalizers: fix em/en dashes → '-', strip, lowercase, and slugify
        import re as _re
        def _norm_dashes(s: str) -> str:
            s = str(s or '')
            return s.replace('—', '-').replace('–', '-').strip().lower()

        def _slug(s: str) -> str:
            s = _norm_dashes(s)
            s = _re.sub(r'[^a-z0-9\s-]', '', s)
            s = _re.sub(r'[\s-]+', '_', s)
            return s.strip('_')

        # 1) live price snapshot (from price_store)
        price_snapshot = 0.0
        price_snapshot_updated_at = 0
        try:
            stations = price_store.list_stations()
            match = None
            target_norm = _norm_dashes(station_name)
            target_slug = _slug(station_name)
            for s in stations:
                if _norm_dashes(s.get("id")) == target_norm:
                    match = s; break
            if match is None:
                for s in stations:
                    if _norm_dashes(s.get("name")) == target_norm:
                        match = s; break
            if match is None:
                for s in stations:
                    if _slug(s.get("name")) == target_slug:
                        match = s; break
            if match:
                price_snapshot = float(match.get("price_php_per_liter") or 0)
                price_snapshot_updated_at = int(match.get("updated_at") or 0)
        except Exception as _e:
            print("⚠️ price snapshot error:", _e)

        # 2) live discount snapshot (from discount_store)
        dpl_snapshot = 0.0
        dpl_captured_at = int(datetime.utcnow().timestamp())
        try:
            val = discount_store.get(station_name)
            if val is None:
                all_discounts = discount_store.get_all() or {}
                for k, v in all_discounts.items():
                    if _norm_dashes(k) == target_norm or _slug(k) == target_slug:
                        val = v; break
            if val is not None:
                dpl_snapshot = float(val)
        except Exception as _e:
            print("⚠️ discount snapshot error:", _e)

        print(f"[BOOK] snapshots: {price_snapshot} {dpl_snapshot} {price_snapshot_updated_at} {dpl_captured_at} (station='{station_name}')")

        row = {
            'account_code': account_code,
            'station': station_name,
            'requested_amount_php': float(request.form.get('requested_amount_php') or 0),
            'refuel_datetime': refuel_dt_str,  # keep original string

            'driver_name': driver_data['driver_name'],
            'vehicle_plate': driver_data['vehicle_plate'],
            'truck_make': driver_data['truck_make'],
            'truck_model': driver_data['truck_model'],
            'number_of_wheels': driver_data['number_of_wheels'],
            'fuel_type': driver_data['fuel_type'],

            'contact_name': request.form.get('contact_number').split('–')[0].strip(),
            'contact_number': request.form.get('contact_number').split('–')[-1].strip(),

            # snapshots
            'price_snapshot_php_per_liter': price_snapshot,
            'price_snapshot_updated_at': price_snapshot_updated_at,
            'discount_snapshot_php_per_liter': dpl_snapshot,
            'discount_snapshot_captured_at': dpl_captured_at,

            'status': 'Unverified',
            'created_at': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            'updated_at': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # save booking
        try:
            created = repo.create_unverified_booking(row)
            print("[BOOK] created voucher:", created.get("voucher_id"))
        except Exception as e:
            print("⚠️ Failed to create Unverified booking:", e)

        preset_path = f"data/presets/{account_code}_presets.csv"
        existing = pd.read_csv(preset_path, encoding='utf-8-sig') if os.path.isfile(preset_path) else pd.DataFrame()
        plate_key = str(driver_data['vehicle_plate']).strip().upper()
        exists = (
            'vehicle_plate' in existing.columns
            and existing['vehicle_plate'].astype(str).str.strip().str.upper().eq(plate_key).any()
        )
        if not exists:
            updated = pd.concat([existing, pd.DataFrame([driver_data])], ignore_index=True)
            updated.to_csv(preset_path, index=False, encoding='utf-8-sig')

        due_amount = request.form.get('requested_amount_php')
        return render_template('booking_success.html', payment_info=PAYMENT_INFO, due_amount=due_amount)

    # GET: blank form (include min_refuel hint)
    return render_template('book.html', customer=None, presets=[], station_names=station_names, min_refuel=min_refuel)

@app.route('/discount-locator')
def discount_locator():
    try:
        stations = pd.read_csv('data/stations.csv', encoding='utf-8-sig').to_dict(orient='records')
    except Exception as e:
        print(f"⚠️ Error loading station list: {e}")
        stations = []
    return render_template('locator.html', stations=stations)

# ======= SUPPLIER API (parity with CSV export) =======
@app.route('/supplier-api/<voucher_id>', methods=['GET'])
def supplier_api(voucher_id):
    token = request.args.get("token")
    if token != SUPPLIER_API_TOKEN:
        return {"error": "Unauthorized – Invalid or missing token."}, 403

    try:
        r = repo.get_voucher(voucher_id)
        if not r:
            return {"error": f"Voucher ID '{voucher_id}' not found."}, 404

        # Snapshot-first values with legacy fallback
        req   = _coalesce(r.get("requested_amount_php"), 0) or 0
        price = _coalesce(r.get("price_snapshot_php_per_liter"), r.get("live_price_php_per_liter"))
        disc  = _coalesce(r.get("discount_snapshot_php_per_liter"), r.get("discount_per_liter"))

        disc_total  = _coalesce(r.get("discount_total_php"),  r.get("discount_total"))
        total_value = _coalesce(r.get("total_dispensed_php"), r.get("total_dispensed"))

        liters_req = r.get("liters_requested")
        try:
            if (liters_req is None or str(liters_req).strip() == "") and price not in (None, "", "nan"):
                p = float(price)
                if p > 0:
                    liters_req = round(float(req) / p, 2)
        except Exception:
            pass

        liters_disp = r.get("liters_dispensed")
        try:
            if (liters_disp is None or str(liters_disp).strip() == "") and price not in (None, "", "nan"):
                p = float(price)
                dt = float(disc_total or 0)
                if p > 0 and liters_req not in (None, "", "nan"):
                    liters_disp = round(float(liters_req) + dt / p, 2)
        except Exception:
            pass

        ts = r.get("refuel_datetime") or r.get("expected_refill_date") or r.get("transaction_date")
        refuel_date_mnl = manila_time_filter(ts)

        return {
            "customer": "UniFleet",
            "fuelProduct": "Diesel",
            "invoice": r.get("voucher_id", ""),
            "station": r.get("station", ""),
            "pricePhpPerLiter": float(price) if price not in (None, "", "nan") else None,
            "discountPhpPerLiter": float(disc) if disc not in (None, "", "nan") else None,
            "requestedAmountPhp": float(req),
            "freeFuelValuePhp": float(disc_total) if disc_total not in (None, "", "nan") else None,
            "totalValuePhp": float(total_value) if total_value not in (None, "", "nan") else None,
            "litersRequested": float(liters_req) if liters_req not in (None, "", "nan") else None,
            "litersDispensed": float(liters_disp) if liters_disp not in (None, "", "nan") else None,
            "driver": r.get("driver_name", ""),
            "plate": r.get("vehicle_plate", ""),
            "status": r.get("status", "") or "Unknown",
            "refuelDate": refuel_date_mnl
        }
    except Exception as e:
        return {"error": f"Unable to process request: {str(e)}"}, 500

# ======= SUPPLIER CSV (parity with Supplier API) =======
@app.route('/export_supplier_csv')
def export_supplier_csv():
    """
    CSV parity with supplier API using snapshot math.
    Columns:
      Customer, Fuel Product, Invoice, Station,
      Price (₱/L), Discount (₱/L), Requested (₱),
      Free Fuel Value (₱), Total Value (₱),
      Liters Requested, Liters Dispensed,
      Driver, Plate, Status, Refuel Date
    """
    try:
        rows = repo.list_all_vouchers()
        if not rows:
            return "<h2>No vouchers to export.</h2>", 200

        out_rows = []
        for r in rows:
            vid   = str(r.get("voucher_id", "")).strip()
            stat  = r.get("station", "") or ""
            req   = _coalesce(r.get("requested_amount_php"), 0) or 0

            price = _coalesce(r.get("price_snapshot_php_per_liter"), r.get("live_price_php_per_liter"))
            disc  = _coalesce(r.get("discount_snapshot_php_per_liter"), r.get("discount_per_liter"))

            disc_total  = _coalesce(r.get("discount_total_php"),  r.get("discount_total"))
            total_value = _coalesce(r.get("total_dispensed_php"), r.get("total_dispensed"))

            liters_req = r.get("liters_requested")
            try:
                if (liters_req is None or str(liters_req).strip() == "") and price not in (None, "", "nan"):
                    p = float(price)
                    if p > 0:
                        liters_req = round(float(req) / p, 2)
            except Exception:
                pass

            liters_disp = r.get("liters_dispensed")
            try:
                if (liters_disp is None or str(liters_disp).strip() == "") and price not in (None, "", "nan"):
                    p = float(price)
                    dt = float(disc_total or 0)
                    if p > 0 and liters_req not in (None, "", "nan"):
                        liters_disp = round(float(liters_req) + dt / p, 2)
            except Exception:
                pass

            ts = r.get("refuel_datetime") or r.get("expected_refill_date") or r.get("transaction_date")
            refuel_date_mnl = manila_time_filter(ts)

            # New: redeemed timestamp (Manila)
            redeemed_ts = r.get("redemption_timestamp")
            redeemed_mnl = manila_time_filter(redeemed_ts)

            
            out_rows.append({
                "Customer": "UniFleet",
                "Fuel Product": "Diesel",
                "Invoice": vid,
                "Station": stat,
                "Price (₱/L)": _fmt_money(price),
                "Discount (₱/L)": _fmt_money(disc),
                "Requested (₱)": _fmt_money(req),
                "Free Fuel Value (₱)": _fmt_money(disc_total),
                "Total Value (₱)": _fmt_money(total_value),
                "Liters Requested": _fmt_money(liters_req),
                "Liters Dispensed": _fmt_money(liters_disp),
                "Driver": r.get("driver_name", "") or "",
                "Plate": r.get("vehicle_plate", "") or "",
                "Status": r.get("status", "") or "",
                "Refuel Date": refuel_date_mnl,
                "Redeemed At": redeemed_mnl,  # <-- NEW COLUMN
            })


        export_path = 'data/supplier_export.csv'
        pd.DataFrame(out_rows).to_csv(export_path, index=False, encoding='utf-8-sig')
        return send_file(export_path, as_attachment=True)
    except Exception as e:
        return f"<h2>Failed to export supplier CSV: {str(e)}</h2>", 500

# =========================
# Admin: Live Prices (pre-DB)
# =========================
discount_store = DiscountStore()

@app.route("/admin/prices")
def admin_prices():
    if not _check_admin_key(request):
        return abort(403)
    stations = price_store.list_stations()
    stations = sorted(stations, key=lambda s: (s.get("brand",""), s.get("name","")))
    discounts = discount_store.get_all()
    return render_template("admin_prices.html", stations=stations, discounts=discounts)

@app.route("/admin/prices/update", methods=["POST"])
def admin_prices_update():
    if not _check_admin_key(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        payload = request.get_json(force=True) or {}
        station_id = str(payload.get("station_id", "")).strip()
        new_price = float(payload.get("price", 0))

        before = price_store.get_station(station_id) or {}
        old_price = before.get("price_php_per_liter")

        updated = price_store.set_price(station_id, new_price)

        append_price_history(
            station_id=station_id,
            old_price=old_price,
            new_price=updated["price_php_per_liter"],
            updated_unix=updated["updated_at"]
        )

        return jsonify({
            "ok": True,
            "station_id": station_id,
            "price_php_per_liter": updated["price_php_per_liter"],
            "updated_at": updated["updated_at"],
        })
    except KeyError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception:
        return jsonify({"ok": False, "error": "server_error"}), 500

# Read-only API for previews
@app.route("/api/v1/prices", methods=["GET"])
def api_prices_list():
    stations = price_store.list_stations()
    return jsonify({"stations": stations})

# =========================
# Discounts
# =========================
@app.route("/admin/discounts/update", methods=["POST"])
def admin_discounts_update():
    if not _check_admin_key(request):
        return abort(403)

    key = request.args.get("key", "").strip()
    station = (request.form.get("station") or "").strip()
    raw_value = (request.form.get("discount_per_liter") or "").strip()

    def _back():
        target = url_for("admin_prices")
        if key:
            target = f"{target}?key={key}"
        return redirect(target)

    if not station:
        flash("Station is required.", "error")
        return _back()

    if raw_value == "":
        flash(f"No changes saved for “{station}”.", "info")
        return _back()

    try:
        value = float(raw_value)
    except ValueError:
        flash("Discount must be a number.", "error")
        return _back()

    if value < 0 or value > 15:
        flash("Discount must be between 0 and 15 PHP/L.", "error")
        return _back()

    try:
        discount_store.set(station, value, actor="admin", reason="manual update")
        flash(f"Saved discount {value:.2f} PHP/L for “{station}”.", "success")
    except DiscountValueError as e:
        flash(str(e), "error")
    except Exception as e:
        flash(f"Failed to save discount: {e}", "error")

    return _back()

@app.route("/api/v1/discounts", methods=["GET"])
def api_discounts_list():
    try:
        return jsonify({"discounts": discount_store.get_all()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# Price Preview API
# =========================
@app.route("/api/v1/price_preview", methods=["GET"])
def api_price_preview():
    """
    Query params:
      - station: station id OR station name (exact match)
      - amount: PHP amount (float)
      - discount_per_liter: optional, default 0 (float)
    """
    station_q = (request.args.get("station") or "").strip()
    try:
        amount = float(request.args.get("amount", "0"))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid amount"}), 400
    try:
        dpl = float(request.args.get("discount_per_liter", "0") or 0)
    except ValueError:
        dpl = 0.0

    def _norm(s): return str(s or "").strip().lower()
    stations = price_store.list_stations()
    match = None
    for s in stations:
        if _norm(s.get("id")) == _norm(station_q):
            match = s
            break
    if match is None:
        for s in stations:
            if _norm(s.get("name")) == _norm(station_q):
                match = s
                break
    if match is None:
        return jsonify({"ok": False, "error": "station not found"}), 404

    try:
        price = float(match.get("price_php_per_liter") or 0)
    except Exception:
        price = 0.0
    ts = int(match.get("updated_at", 0) or 0)

    if amount <= 0 or price <= 0:
        return jsonify({"ok": False, "error": "invalid amount or price"}), 400

    liters_requested = round(amount / price, 2)
    discount_total = round(liters_requested * dpl, 2)
    total_dispensed = round(amount + discount_total, 2)
    liters_dispensed = round(liters_requested + (discount_total / price if price else 0), 2)

    is_stale = False
    if ts <= 0:
        is_stale = True
    else:
        now = int(datetime.now().timestamp())
        is_stale = (now - ts) >= 7 * 24 * 60 * 60

    return jsonify({
        "ok": True,
        "station_id": match.get("id"),
        "station_name": match.get("name"),
        "price_php_per_liter": price,
        "price_updated_at": ts,
        "price_is_stale": is_stale,
        "requested_amount_php": amount,
        "discount_per_liter": dpl,
        "liters_requested": liters_requested,
        "discount_total": discount_total,
        "total_dispensed": total_dispensed,
        "liters_dispensed": liters_dispensed
    })

# =========================
# PDF Preferences + Export
# =========================
@app.route("/pdf/prefs", methods=["POST"])
def save_pdf_prefs():
    """
    Persist selected station ids for the PDF (checkboxes on /form).
    Expects one or more form fields named 'station_id'.
    Stores in a cookie 'pdf_station_ids' comma-separated for ~6 months.
    - If the POST is empty (no boxes checked), we KEEP the previous cookie.
    """
    # What the user posted
    posted_ids = request.form.getlist("station_id")
    posted_ids = [s.strip() for s in posted_ids if s.strip()]

    # If nothing posted, keep previous cookie; else use the posted list
    if posted_ids:
        cookie_val = "|".join(posted_ids)
    else:
        # keep prior cookie value (empty string if none existed)
        cookie_val = request.cookies.get("pdf_station_ids", "")

    resp = make_response(redirect(url_for("form")))
    # IMPORTANT: set a path so cookie is sent back on /form and /supplier-sheet.pdf
    print("Setting cookie:", cookie_val)
    resp.set_cookie(
        "pdf_station_ids",
        cookie_val,
        max_age=60 * 60 * 24 * 30 * 6,  # ~6 months
        path="/",
        samesite="Lax",
        secure=False  # flip to True if you have a custom HTTPS domain; fine on Replit either way
    )
    return resp


@app.route("/supplier-sheet.pdf", methods=["GET"])
def supplier_sheet_pdf():
    """
    Build and return the supplier PDF.
    Station filter resolves as:
      1) any ?station=<id> query params (one or many),
      2) else cookie 'pdf_station_ids',
      3) else all stations.
    """
    # Guard: if PDF builder is missing, don’t crash — show a clear error
    if build_supplier_pdf is None:
        return (
            f"<h2>PDF generator not available.</h2>"
            f"<p>Import error: {_PDF_IMPORT_ERROR}</p>"
            f"<p>Ensure <code>report_pdf.py</code> defines "
            f"<code>build_supplier_pdf(vouchers, target_station_ids, stations, logo_path)</code>.</p>",
            500,
        )

    # 1) explicit query selection
    query_station_ids = request.args.getlist("station")
    query_station_ids = [s.strip() for s in query_station_ids if s.strip()]

    # 2) cookie fallback
    cookie_val = request.cookies.get("pdf_station_ids", "")
    cookie_val_norm = cookie_val.replace("|", ",")
    cookie_station_ids = [s.strip() for s in cookie_val_norm.split(",") if s.strip()]

    # 3) default to all
    all_stations = price_store.list_stations()
    all_ids = [s.get("id") for s in all_stations if s.get("id")]

    selected_ids = query_station_ids or cookie_station_ids or all_ids

    # Build PDF in-memory (Unredeemed only)
    rows = repo.list_all_vouchers()
    vouchers = [r for r in rows if (r.get("status") or "").strip() == "Unredeemed"]
    pdf_bytes = build_supplier_pdf(
        vouchers=vouchers,
        target_station_ids=set(selected_ids),
        stations=all_stations,
        logo_path="static/UniFleet Logo.png",
    )

    # Manila-dated filename for uniqueness
    dated = datetime.now(ZoneInfo("Asia/Manila")).strftime("%b-%d-%Y")  # e.g., Sep-12-2025
    filename = f"UniFleet_Offline_Voucher_List_{dated}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )

# =========================
# Entrypoint
# =========================
if __name__ == "__main__":
    # Useful for local debugging
    app.run(host="0.0.0.0", port=5000, debug=True)
