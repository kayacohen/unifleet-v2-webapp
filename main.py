from flask import Flask, render_template, request, redirect, send_file, abort, url_for, flash
import os
import subprocess
import pandas as pd
from datetime import date, datetime
from zoneinfo import ZoneInfo  # <<< ADDED for PH timezone
import random
import string
import csv
import re

# >>> ADD: admin prices imports
from flask import jsonify
import price_store

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Required for flashing messages

SUPPLIER_API_TOKEN = os.environ.get("SUPPLIER_API_TOKEN", "unifleet2025mvp")  # Default token

# >>> ADD: simple admin key for the prices page
ADMIN_KEY = os.environ.get("ADMIN_KEY", "unifleet-admin")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs("data/presets", exist_ok=True)

# >>> ADD: initialize price store JSON on startup (creates data/station_prices.json if missing)
price_store.init_if_missing()

# ===== Runtime flags / tokens (optional) =====
ENFORCE_PHASES = os.environ.get("ENFORCE_PHASES", "").strip() == "1"
OPS_TOKEN = os.environ.get("OPS_TOKEN", "").strip()

# ===== Payment instructions config =====
PAYMENT_INFO = {
    "unionbank": {
        "label": "UnionBank",
        "account_name": "UniFleet Inc.",
        "account_number": "1234-5678-9012",  # <-- replace with real
        # "routing_number": "XXXXXX",
    },
    "gcash": {
        "label": "GCash",
        "account_name": "UniFleet Inc.",
        "account_number": "0945-149-2369",   # <-- replace with real
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
                # <<< CHANGED: force PH time for human-readable column
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

# >>> ADD: simple admin guard
def _check_admin_key(req):
    key = req.args.get("key") or req.headers.get("X-Admin-Key")
    return key == ADMIN_KEY

@app.route('/')
def home():
    return redirect("/form")

@app.route('/form')
def form():
    try:
        df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
        df = df.sort_values(by='transaction_date', ascending=False).head(50)
        vouchers = df.to_dict(orient='records')
        for row in vouchers:
            vid = str(row.get("voucher_id", "")).strip()
            png_1 = os.path.exists(f"static/qr_codes/{vid}.png")
            png_2 = os.path.exists(f"static/qr_codes/{vid}_Official.png")
            row['png_exists'] = png_1 and png_2
    except Exception as e:
        print(f"⚠️ Error loading vouchers: {e}")
        vouchers = []
    return render_template("form.html", today=date.today().isoformat(), vouchers=vouchers)

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
    df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
    df = _ensure_voucher_columns(df)
    voucher = df[df['voucher_id'] == voucher_id]
    if voucher.empty:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    row = voucher.iloc[0].to_dict()
    return render_template('redeem.html', voucher=row)

@app.route('/redeem/<voucher_id>', methods=['POST'])
def mark_redeemed(voucher_id):
    df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
    df = _ensure_voucher_columns(df)
    if voucher_id not in df['voucher_id'].values:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    current_status = str(df.loc[df['voucher_id'] == voucher_id, 'status'].iloc[0]).strip()
    allowed = (current_status in ('', 'Unverified', 'Unredeemed'))
    if ENFORCE_PHASES:
        allowed = (current_status == 'Unredeemed')
    if not allowed:
        append_audit("redeem_denied", voucher_id, current_status, "Redeemed", f"enforce_phases={int(ENFORCE_PHASES)}")
        return f"<h2>Cannot redeem voucher while status is '{current_status or 'Unverified'}'.</h2>", 400
    ts = datetime.now().isoformat(timespec='seconds')
    df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = ['Redeemed', ts]
    df.to_csv('data/master_vouchers.csv', index=False, encoding='utf-8-sig')
    append_audit("redeem_success", voucher_id, current_status, "Redeemed", f"enforce_phases={int(ENFORCE_PHASES)}")
    return redirect(f"/redeem/{voucher_id}")

@app.route('/ops/voucher/<voucher_id>/status/<new_status>', methods=['GET'])
def ops_set_status(voucher_id, new_status):
    if OPS_TOKEN and request.args.get("token", "") != OPS_TOKEN:
        return "<h2>Forbidden: invalid token.</h2>", 403
    allowed_targets = {'Unverified', 'Unredeemed', 'Redeemed'}
    if new_status not in allowed_targets:
        return f"<h2>Invalid status '{new_status}'.</h2>", 400
    df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
    df = _ensure_voucher_columns(df)
    if voucher_id not in df['voucher_id'].values:
        return f"<h2>Voucher ID '{voucher_id}' not found.</h2>", 404
    prev = str(df.loc[df['voucher_id'] == voucher_id, 'status'].iloc[0]).strip()
    if new_status == 'Redeemed':
        ts = datetime.now().isoformat(timespec='seconds')
        df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = ['Redeemed', ts]
    else:
        df.loc[df['voucher_id'] == voucher_id, 'status'] = new_status
        df.loc[df['voucher_id'] == voucher_id, 'redemption_timestamp'] = ""
    df.to_csv('data/master_vouchers.csv', index=False, encoding='utf-8-sig')
    append_audit("ops_set_status", voucher_id, prev, new_status, f"token_ok={int(bool(not OPS_TOKEN or request.args.get('token','')==OPS_TOKEN))}")
    return redirect(f"/redeem/{voucher_id}")

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
    stations_path = 'data/stations.csv'
    try:
        stations_df = pd.read_csv(stations_path, encoding='utf-8-sig')
        station_names = stations_df['station_name'].dropna().tolist()
    except Exception as e:
        print(f"⚠️ Error loading stations: {e}")
        station_names = []
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
                return render_template('book.html', customer=None, presets=[], station_names=station_names)
            base = rows.iloc[0].to_dict()
            preset_path = f"data/presets/{account_code}_presets.csv"
            presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
            return render_template('book.html', customer=base, presets=presets, station_names=station_names)
        driver_mode = request.form.get('driver_mode')
        use_new = driver_mode == 'new'
        if driver_mode == 'preset' and not request.form.get('driver_select'):
            flash("Please select a preset or switch to 'Add New Driver'", "error")
            base = rows.iloc[0].to_dict()
            preset_path = f"data/presets/{account_code}_presets.csv"
            presets = pd.read_csv(preset_path, encoding='utf-8-sig').to_dict(orient='records') if os.path.isfile(preset_path) else []
            return render_template('book.html', customer=base, presets=presets, station_names=station_names, form_values=request.form)
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
        row = {
            'account_code': account_code,
            'station': request.form.get('station'),
            'requested_amount_php': request.form.get('requested_amount_php'),
            'refuel_datetime': request.form.get('refuel_datetime'),
            'driver_name': driver_data['driver_name'],
            'vehicle_plate': driver_data['vehicle_plate'],
            'truck_make': driver_data['truck_make'],
            'truck_model': driver_data['truck_model'],
            'number_of_wheels': driver_data['number_of_wheels'],
            'fuel_type': driver_data['fuel_type'],
            'contact_name': request.form.get('contact_number').split('–')[0].strip(),
            'contact_number': request.form.get('contact_number').split('–')[-1].strip()
        }
        preset_path = f"data/presets/{account_code}_presets.csv"
        existing = pd.read_csv(preset_path, encoding='utf-8-sig') if os.path.isfile(preset_path) else pd.DataFrame()
        if driver_data['vehicle_plate'] not in existing.get('vehicle_plate', []):
            updated = pd.concat([existing, pd.DataFrame([driver_data])])
            updated.to_csv(preset_path, index=False, encoding='utf-8-sig')
        due_amount = request.form.get('requested_amount_php')
        return render_template('booking_success.html', payment_info=PAYMENT_INFO, due_amount=due_amount)
    return render_template('book.html', customer=None, presets=[], station_names=station_names)

@app.route('/discount-locator')
def discount_locator():
    try:
        stations = pd.read_csv('data/stations.csv', encoding='utf-8-sig').to_dict(orient='records')
    except Exception as e:
        print(f"⚠️ Error loading station list: {e}")
        stations = []
    return render_template('locator.html', stations=stations)

@app.route('/supplier-api/<voucher_id>', methods=['GET'])
def supplier_api(voucher_id):
    # Check token before proceeding
    token = request.args.get("token")
    if token != SUPPLIER_API_TOKEN:
        return {"error": "Unauthorized – Invalid or missing token."}, 403

    try:
        df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
        print(df[['voucher_id', 'status']].tail(10))
        row_df = df[df['voucher_id'] == voucher_id]
        if row_df.empty:
            return {"error": f"Voucher ID '{voucher_id}' not found."}, 404
        row = row_df.iloc[0].to_dict()
        response = {
            "Customer": "UniFleet",
            "Fuel Product": "Diesel",
            "Qty": float(row.get("liters_requested", 0)),
            "Driver": row.get("driver_name", ""),
            "Plate": row.get("vehicle_plate", ""),
            "Invoice": row.get("voucher_id", ""),
            "Status": row.get("status", "Unknown")
        }
        return response
    except Exception as e:
        return {"error": f"Unable to process request: {str(e)}"}, 500

@app.route('/export_supplier_csv')
def export_supplier_csv():
    try:
        df = pd.read_csv('data/master_vouchers.csv', encoding='utf-8-sig')
        df = _ensure_voucher_columns(df)
        export_df = df.copy()
        export_df = export_df[[
            'voucher_id',
            'driver_name',
            'vehicle_plate',
            'liters_requested',
            'status'
        ]].rename(columns={
            'voucher_id': 'Invoice',
            'driver_name': 'Driver',
            'vehicle_plate': 'Plate',
            'liters_requested': 'Qty',
            'status': 'Status'
        })
        export_df.insert(0, 'Customer', 'UniFleet')
        export_df.insert(2, 'Fuel Product', 'Diesel')
        export_path = 'data/supplier_export.csv'
        export_df.to_csv(export_path, index=False, encoding='utf-8-sig')
        return send_file(export_path, as_attachment=True)
    except Exception as e:
        return f"<h2>Failed to export supplier CSV: {str(e)}</h2>", 500

# =========================
# Admin: Live Prices (pre-DB)
# =========================
@app.route("/admin/prices")
def admin_prices():
    if not _check_admin_key(request):
        return abort(403)
    stations = price_store.list_stations()
    stations = sorted(stations, key=lambda s: (s.get("brand",""), s.get("name","")))
    return render_template("admin_prices.html", stations=stations)

@app.route("/admin/prices/update", methods=["POST"])
def admin_prices_update():
    if not _check_admin_key(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        payload = request.get_json(force=True) or {}
        station_id = str(payload.get("station_id", "")).strip()
        new_price = float(payload.get("price", 0))

        # capture old price before change
        before = price_store.get_station(station_id) or {}
        old_price = before.get("price_php_per_liter")

        # perform update (sets updated_at = epoch seconds)
        updated = price_store.set_price(station_id, new_price)

        # append a CSV audit row
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

# Read-only API (handy for previews & step #5)
@app.route("/api/v1/prices", methods=["GET"])
def api_prices_list():
    stations = price_store.list_stations()
    return jsonify({"stations": stations})

# =========================
# Price Preview API (always uses stored price; flags stale)
# =========================
@app.route("/api/v1/price_preview", methods=["GET"])  # [1]
def api_price_preview():  # [2]
    """
    Query params:  # [3]
      - station: station id OR station name (exact match)  # [4]
      - amount: PHP amount (float)  # [5]
      - discount_per_liter: optional, default 0 (float)  # [6]
    """  # [7]
    station_q = (request.args.get("station") or "").strip()  # [8]
    try:  # [9]
        amount = float(request.args.get("amount", "0"))  # [10]
    except ValueError:  # [11]
        return jsonify({"ok": False, "error": "invalid amount"}), 400  # [12]
    try:  # [13]
        dpl = float(request.args.get("discount_per_liter", "0") or 0)  # [14]
    except ValueError:  # [15]
        dpl = 0.0  # [16]

    # Resolve station by id, then by name (case-insensitive)  # [17]
    def _norm(s): return str(s or "").strip().lower()  # [18]
    stations = price_store.list_stations()  # [19]
    match = None  # [20]
    for s in stations:  # [21]
        if _norm(s.get("id")) == _norm(station_q):  # [22]
            match = s; break  # [23]
    if match is None:  # [24]
        for s in stations:  # [25]
            if _norm(s.get("name")) == _norm(station_q):  # [26]
                match = s; break  # [27]
    if match is None:  # [28]
        return jsonify({"ok": False, "error": "station not found"}), 404  # [29]

    # Always use stored price; compute a simple "stale" flag (>= 7 days)  # [30]
    try:  # [31]
        price = float(match.get("price_php_per_liter") or 0)  # [32]
    except Exception:  # [33]
        price = 0.0  # [34]
    ts = int(match.get("updated_at", 0) or 0)  # [35]

    if amount <= 0 or price <= 0:  # [36]
        return jsonify({"ok": False, "error": "invalid amount or price"}), 400  # [37]

    liters_requested = round(amount / price, 2)  # [38]
    discount_total = round(liters_requested * dpl, 2)  # [39]
    total_dispensed = round(amount + discount_total, 2)  # [40]
    liters_dispensed = round(liters_requested + (discount_total / price if price else 0), 2)  # [41]

    # Informational stale flag (no blocking)  # [42]
    is_stale = False  # [43]
    if ts <= 0:  # [44]
        is_stale = True  # [45]
    else:  # [46]
        now = int(datetime.now().timestamp())  # [47]
        is_stale = (now - ts) >= 7 * 24 * 60 * 60  # [48]

    return jsonify({  # [49]
        "ok": True,  # [50]
        "station_id": match.get("id"),  # [51]
        "station_name": match.get("name"),  # [52]
        "price_php_per_liter": price,  # [53]
        "price_updated_at": ts,  # [54]
        "price_is_stale": is_stale,  # [55]
        "requested_amount_php": amount,  # [56]
        "discount_per_liter": dpl,  # [57]
        "liters_requested": liters_requested,  # [58]
        "discount_total": discount_total,  # [59]
        "total_dispensed": total_dispensed,  # [60]
        "liters_dispensed": liters_dispensed  # [61]
    })  # [62]
