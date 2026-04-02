from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime
import uuid
import sqlite3
import os

app = Flask(__name__)
app.secret_key = "parking_secret_123"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parkease.db")


# ─── Database helpers ──────────────────────────────────────────────────
def get_db():
    """Get a database connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role     TEXT NOT NULL DEFAULT 'customer'
        );
        CREATE TABLE IF NOT EXISTS facilities (
            fid         TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            owner       TEXT NOT NULL,
            total_slots INTEGER NOT NULL,
            rate        REAL NOT NULL,
            FOREIGN KEY (owner) REFERENCES users(username)
        );
        CREATE TABLE IF NOT EXISTS slots (
            fid      TEXT NOT NULL,
            slot_num INTEGER NOT NULL,
            booking_id TEXT,
            PRIMARY KEY (fid, slot_num),
            FOREIGN KEY (fid) REFERENCES facilities(fid) ON DELETE CASCADE,
            FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
        );
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id TEXT PRIMARY KEY,
            customer   TEXT NOT NULL,
            facility_id TEXT NOT NULL,
            slot       INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            start_time TEXT,
            end_time   TEXT,
            status     TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY (customer) REFERENCES users(username),
            FOREIGN KEY (facility_id) REFERENCES facilities(fid)
        );
    """)
    conn.commit()
    conn.close()


# ─── Data conversion helpers ───────────────────────────────────────────
def _parse_dt(s):
    """Parse a datetime string back to a datetime object."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def get_facility_dict(fid):
    """Load a single facility as the dictionary structure templates expect."""
    conn = get_db()
    f = conn.execute("SELECT * FROM facilities WHERE fid=?", (fid,)).fetchone()
    if not f:
        conn.close()
        return None
    slots_rows = conn.execute(
        "SELECT slot_num, booking_id FROM slots WHERE fid=? ORDER BY slot_num", (fid,)
    ).fetchall()
    conn.close()
    slots = {row["slot_num"]: row["booking_id"] for row in slots_rows}
    return {
        "name": f["name"],
        "owner": f["owner"],
        "total_slots": f["total_slots"],
        "rate": f["rate"],
        "slots": slots,
    }


def get_user_facilities(username):
    """Return {fid: facility_dict} for an owner."""
    conn = get_db()
    rows = conn.execute("SELECT fid FROM facilities WHERE owner=?", (username,)).fetchall()
    conn.close()
    result = {}
    for row in rows:
        fd = get_facility_dict(row["fid"])
        if fd:
            result[row["fid"]] = fd
    return result


def get_booking_dict(bid):
    """Load a booking as a dict with datetime objects."""
    conn = get_db()
    b = conn.execute("SELECT * FROM bookings WHERE booking_id=?", (bid,)).fetchone()
    conn.close()
    if not b:
        return None
    return {
        "customer": b["customer"],
        "facility_id": b["facility_id"],
        "slot": b["slot"],
        "entry_time": _parse_dt(b["entry_time"]),
        "start_time": _parse_dt(b["start_time"]),
        "end_time": _parse_dt(b["end_time"]),
        "status": b["status"],
    }


def get_all_bookings_dict():
    """Return {booking_id: booking_dict} for all bookings (used by facility_detail)."""
    conn = get_db()
    rows = conn.execute("SELECT booking_id FROM bookings").fetchall()
    conn.close()
    return {row["booking_id"]: get_booking_dict(row["booking_id"]) for row in rows}


def get_customer_bookings(username):
    """Return {bid: booking_dict} for active bookings of a customer."""
    conn = get_db()
    rows = conn.execute(
        "SELECT booking_id FROM bookings WHERE customer=? AND status='active'", (username,)
    ).fetchall()
    conn.close()
    return {row["booking_id"]: get_booking_dict(row["booking_id"]) for row in rows}


# ─── Auth ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    if "user" in session:
        conn = get_db()
        user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
        conn.close()
        if user:
            return redirect(url_for("owner_dashboard") if user["role"] == "owner" else url_for("customer_dashboard"))
        else:
            session.clear()
    return render_template("index.html")


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "customer")
    if not username or not password:
        return redirect(url_for("index"))
    conn = get_db()
    existing = conn.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        return render_template("index.html", error="Username already exists.")
    conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                 (username, password, role))
    conn.commit()
    conn.close()
    session["user"] = username
    return redirect(url_for("owner_dashboard") if role == "owner" else url_for("customer_dashboard"))


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not user or user["password"] != password:
        return render_template("index.html", error="Invalid credentials.")
    session["user"] = username
    role = user["role"]
    return redirect(url_for("owner_dashboard") if role == "owner" else url_for("customer_dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ─── Owner ─────────────────────────────────────────────────────────────
@app.route("/owner")
def owner_dashboard():
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    conn.close()
    if not user or user["role"] != "owner":
        return redirect(url_for("index"))
    my_facilities = get_user_facilities(session["user"])
    return render_template("owner.html", username=session["user"], facilities=my_facilities)


@app.route("/owner/add_facility", methods=["POST"])
def add_facility():
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    if not user or user["role"] != "owner":
        conn.close()
        return redirect(url_for("index"))
    name = request.form.get("name", "").strip()
    total_slots = int(request.form.get("total_slots", 10))
    rate = float(request.form.get("rate", 20))
    if not name:
        conn.close()
        return redirect(url_for("owner_dashboard"))
    fid = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO facilities (fid, name, owner, total_slots, rate) VALUES (?, ?, ?, ?, ?)",
        (fid, name, session["user"], total_slots, rate)
    )
    # Create slot rows
    for i in range(1, total_slots + 1):
        conn.execute("INSERT INTO slots (fid, slot_num, booking_id) VALUES (?, ?, NULL)", (fid, i))
    conn.commit()
    conn.close()
    return redirect(url_for("owner_dashboard"))


@app.route("/owner/facility/<fid>")
def facility_detail(fid):
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    conn.close()
    if not user or user["role"] != "owner":
        return redirect(url_for("index"))
    f = get_facility_dict(fid)
    if not f or f["owner"] != session["user"]:
        return redirect(url_for("owner_dashboard"))
    occupied = sum(1 for s in f["slots"].values() if s is not None)
    all_bookings = get_all_bookings_dict()
    return render_template("facility_detail.html", fid=fid, facility=f, occupied=occupied, bookings=all_bookings)


@app.route("/owner/delete_facility/<fid>", methods=["POST"])
def delete_facility(fid):
    """Owner can delete (remove) their own facility."""
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    if not user or user["role"] != "owner":
        conn.close()
        return redirect(url_for("index"))
    f = conn.execute("SELECT * FROM facilities WHERE fid=? AND owner=?", (fid, session["user"])).fetchone()
    if not f:
        conn.close()
        return redirect(url_for("owner_dashboard"))
    # Mark any active bookings for this facility as completed
    conn.execute("UPDATE bookings SET status='completed' WHERE facility_id=? AND status='active'", (fid,))
    # Delete slots (cascaded) and facility
    conn.execute("DELETE FROM slots WHERE fid=?", (fid,))
    conn.execute("DELETE FROM facilities WHERE fid=?", (fid,))
    conn.commit()
    conn.close()
    return redirect(url_for("owner_dashboard"))


# ─── Customer ──────────────────────────────────────────────────────────
@app.route("/customer")
def customer_dashboard():
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    conn.close()
    if not user or user["role"] != "customer":
        return redirect(url_for("index"))
    my_bookings = get_customer_bookings(session["user"])
    enriched = []
    for bid, b in my_bookings.items():
        f = get_facility_dict(b["facility_id"])
        enriched.append({
            **b, "bid": bid,
            "facility_name": f["name"] if f else "Unknown",
            "rate": f["rate"] if f else 0,
        })
    return render_template("customer.html", username=session["user"], bookings=enriched)


@app.route("/customer/search")
def search():
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    conn.close()
    if not user or user["role"] != "customer":
        return redirect(url_for("index"))
    query = request.args.get("q", "").strip().lower()
    conn = get_db()
    rows = conn.execute("SELECT fid, name, total_slots, rate FROM facilities").fetchall()
    conn.close()
    results = []
    for row in rows:
        if query in row["name"].lower():
            fd = get_facility_dict(row["fid"])
            available = sum(1 for s in fd["slots"].values() if s is None)
            results.append({**fd, "fid": row["fid"], "available": available})
    return render_template("search.html", results=results, query=query, username=session["user"])


@app.route("/customer/book/<fid>", methods=["POST"])
def book_slot(fid):
    if "user" not in session:
        return redirect(url_for("index"))
    conn = get_db()
    user = conn.execute("SELECT role FROM users WHERE username=?", (session["user"],)).fetchone()
    if not user or user["role"] != "customer":
        conn.close()
        return redirect(url_for("index"))
    f = conn.execute("SELECT * FROM facilities WHERE fid=?", (fid,)).fetchone()
    if not f:
        conn.close()
        return redirect(url_for("customer_dashboard"))

    start_time_str = request.form.get("start_time", "").strip()
    end_time_str = request.form.get("end_time", "").strip()
    today = datetime.now().date()
    try:
        start_time = datetime.strptime(f"{today} {start_time_str}", "%Y-%m-%d %H:%M")
        end_time = datetime.strptime(f"{today} {end_time_str}", "%Y-%m-%d %H:%M")
        if end_time <= start_time:
            conn.close()
            return redirect(url_for("customer_dashboard"))
    except ValueError:
        start_time = datetime.now()
        # hello 
        end_time = None

    # Find first free slot
    free_slot = conn.execute(
        "SELECT slot_num FROM slots WHERE fid=? AND booking_id IS NULL ORDER BY slot_num LIMIT 1",
        (fid,)
    ).fetchone()
    if not free_slot:
        conn.close()
        return redirect(url_for("search") + "?q=")

    slot_num = free_slot["slot_num"]
    bid = str(uuid.uuid4())[:8]
    now = datetime.now()
    conn.execute(
        "INSERT INTO bookings (booking_id, customer, facility_id, slot, entry_time, start_time, end_time, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
        (bid, session["user"], fid, slot_num,
         now.strftime("%Y-%m-%d %H:%M:%S"),
         start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time else None,
         end_time.strftime("%Y-%m-%d %H:%M:%S") if end_time else None)
    )
    conn.execute("UPDATE slots SET booking_id=? WHERE fid=? AND slot_num=?", (bid, fid, slot_num))
    conn.commit()
    conn.close()
    return redirect(url_for("customer_dashboard"))


@app.route("/customer/release/<bid>")
def release_slot(bid):
    if "user" not in session:
        return redirect(url_for("index"))
    b = get_booking_dict(bid)
    if not b or b["customer"] != session["user"]:
        return redirect(url_for("customer_dashboard"))

    # Calculate hours from the booked time window if available
    if b.get("end_time") and b.get("start_time"):
        diff = b["end_time"] - b["start_time"]
        hours = max(1, -(-diff.total_seconds() // 3600))  # ceiling division
    else:
        duration = datetime.now() - b["entry_time"]
        hours = max(1, int(duration.total_seconds() / 3600) + 1)

    f = get_facility_dict(b["facility_id"])
    fee = hours * (f["rate"] if f else 20)

    conn = get_db()
    conn.execute("UPDATE slots SET booking_id=NULL WHERE fid=? AND slot_num=?",
                 (b["facility_id"], b["slot"]))
    conn.execute("UPDATE bookings SET status='completed' WHERE booking_id=?", (bid,))
    conn.commit()
    conn.close()

    return render_template("receipt.html", booking=b, fee=fee, hours=hours,
                           facility_name=f["name"] if f else "", username=session["user"])


# ─── Initialize DB and run ─────────────────────────────────────────────
init_db()

if __name__ == "__main__":
    app.run(debug=True)
