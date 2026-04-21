"""
╔══════════════════════════════════════════════════════════╗
║  NUWAY — Retail Manager Lookup API                       ║
║  Runs locally on each store's PC                         ║
║  Connects to Retail Manager's recent.mdb via ODBC        ║
║  Serves invoice/customer data to the Nuway web app       ║
╚══════════════════════════════════════════════════════════╝

SETUP:
  1. Install Python 3.8+ from python.org (check "Add to PATH")
  2. Open Command Prompt and run:
       pip install flask flask-cors pyodbc
  3. Edit the MDB_PATH below to point to your store's recent.mdb
  4. Double-click this file (or run: python nuway_rm_api.py)
  5. The API runs at http://localhost:5555

ENDPOINTS:
  GET /health              — Check if API is running
  GET /lookup?invoice=41080 — Look up invoice by number, returns customer + products
  GET /customer?q=smith    — Search customers by name
  GET /tables              — List all tables in the database (debug)
  GET /columns?table=Customer — List columns in a table (debug)

The Nuway web app calls these endpoints when staff type an invoice number
in the booking form. Results auto-fill customer name, address, phone,
suburb, products, and payment type. Staff can still edit after auto-fill.
"""

import os
import sys
import json
import traceback

# ═══ CONFIGURATION ═══
# Option 1: Set the path directly here (simplest)
MDB_PATH = r"C:\RetailManager\Data\recent.mdb"

# Option 2: Set your store name here and the script will fetch
# the DB path from Nuway's Supabase settings (if configured there)
STORE_NAME = ""  # e.g. "Ormeau", "Logan" — leave blank to use MDB_PATH above

# Supabase connection (same as the web app)
SB_URL = "https://prgbejzvxbdqnszvofpj.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InByZ2Jlanp2eGJkcW5zenZvZnBqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY2NjU5MjksImV4cCI6MjA5MjI0MTkyOX0.6IpVkprZRlJb4f4yoiiL34bXK2XHEkp4ZeP25Joh3vU"

# Server settings
HOST = "127.0.0.1"  # localhost only — not accessible from other machines
PORT = 5555

def resolve_mdb_path():
    """
    Determine the database path. Priority:
    1. If STORE_NAME is set, fetch rm_db_path from Supabase store_settings
    2. Fall back to the hardcoded MDB_PATH above
    """
    global MDB_PATH
    if STORE_NAME:
        try:
            import urllib.request
            url = f"{SB_URL}/rest/v1/store_settings?store=eq.{STORE_NAME}&select=rm_db_path"
            req = urllib.request.Request(url, headers={
                "apikey": SB_KEY,
                "Authorization": f"Bearer {SB_KEY}"
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data and data[0].get("rm_db_path"):
                    MDB_PATH = data[0]["rm_db_path"]
                    print(f"  DB path from Supabase: {MDB_PATH}")
                    return
        except Exception as e:
            print(f"  Could not fetch path from Supabase: {e}")
            print(f"  Using local MDB_PATH instead")
    # Use the hardcoded path
    print(f"  Using configured path: {MDB_PATH}")

# ═══ DEPENDENCIES ═══
try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    import pyodbc
except ImportError:
    print("=" * 60)
    print("Missing dependencies! Run this command first:")
    print()
    print("  pip install flask flask-cors pyodbc")
    print()
    print("=" * 60)
    input("Press Enter to exit...")
    sys.exit(1)

# ═══ FLASK APP SETUP ═══
app = Flask(__name__)
# Allow CORS from GitHub Pages and localhost for development
CORS(app, origins=[
    "https://chrislookup.github.io",
    "http://localhost:*",
    "http://127.0.0.1:*",
    "null"  # file:// origins
])


def get_db():
    """
    Connect to the Retail Manager Access database.
    Uses the Microsoft Jet ODBC driver (built into Windows).
    Returns a pyodbc connection object.
    """
    if not os.path.exists(MDB_PATH):
        raise FileNotFoundError(f"Database not found: {MDB_PATH}")

    # Try 64-bit driver first, fall back to 32-bit
    drivers = [
        r"Microsoft Access Driver (*.mdb, *.accdb)",  # 64-bit
        r"Microsoft Access Driver (*.mdb)",            # 32-bit / older
    ]
    for driver in drivers:
        try:
            conn_str = f"DRIVER={{{driver}}};DBQ={MDB_PATH};"
            conn = pyodbc.connect(conn_str, readonly=True)
            return conn
        except pyodbc.Error:
            continue

    raise Exception(
        "No Access ODBC driver found. Install Microsoft Access Database Engine:\n"
        "https://www.microsoft.com/en-us/download/details.aspx?id=54920"
    )


# ═══ HEALTH CHECK ═══
@app.route("/health")
def health():
    """Quick check that the API is running and can connect to the database."""
    try:
        conn = get_db()
        conn.close()
        return jsonify({"status": "ok", "database": MDB_PATH})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══ INVOICE LOOKUP ═══
@app.route("/lookup")
def lookup_invoice():
    """
    Look up an invoice (docket) by number.
    Returns customer details + line items + payment info.

    The Nuway web app calls this when staff type an invoice number.
    Query: GET /lookup?invoice=41080

    Response: {
      "invoice": "41080",
      "customer_name": "Smith, John",
      "address": "14 Acacia Drive",
      "suburb": "Loganholme",
      "state": "QLD",
      "postcode": "4129",
      "phone": "0412345678",
      "products": "2m³ Blue Metal\n1x Cement 20kg",
      "payment_type": "Account",
      "total": 840.00
    }
    """
    invoice = request.args.get("invoice", "").strip()
    if not invoice:
        return jsonify({"error": "No invoice number provided"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        # Step 1: Find the docket (invoice) header
        # Try matching docket_id as number, or look in description
        result = {"invoice": invoice}

        # Look up in Docket table
        try:
            cursor.execute("""
                SELECT * FROM Docket
                WHERE docket_id = ?
            """, (int(invoice),))
            docket = cursor.fetchone()

            if docket:
                columns = [col[0] for col in cursor.description]
                docket_dict = dict(zip(columns, docket))
                result["docket_found"] = True
                result["docket"] = {k: str(v) if v is not None else None
                                   for k, v in docket_dict.items()}

                # Step 2: Get customer details using customer_id from docket
                customer_id = docket_dict.get("customer_id")
                if customer_id:
                    try:
                        cursor.execute("SELECT * FROM Customer WHERE customer_id = ?",
                                      (customer_id,))
                        cust = cursor.fetchone()
                        if cust:
                            cols = [col[0] for col in cursor.description]
                            cust_dict = dict(zip(cols, cust))
                            # Extract the fields we need
                            result["customer_name"] = _clean(
                                cust_dict.get("name1", "") or cust_dict.get("Name1", ""))
                            result["customer_name2"] = _clean(
                                cust_dict.get("name2", "") or cust_dict.get("Name2", ""))
                            result["customer_id"] = str(customer_id)
                    except Exception as e:
                        result["customer_error"] = str(e)

                # Step 3: Try to get address from CustomerAddress table
                if customer_id:
                    try:
                        cursor.execute(
                            "SELECT * FROM CustomerAddress WHERE customer_id = ?",
                            (customer_id,))
                        addr = cursor.fetchone()
                        if addr:
                            cols = [col[0] for col in cursor.description]
                            addr_dict = dict(zip(cols, addr))
                            result["address"] = _clean(addr_dict.get("address1", "")
                                                       or addr_dict.get("Address1", ""))
                            result["suburb"] = _clean(addr_dict.get("suburb", "")
                                                      or addr_dict.get("Suburb", ""))
                            result["state"] = _clean(addr_dict.get("state", "")
                                                     or addr_dict.get("State", ""))
                            result["postcode"] = _clean(addr_dict.get("postcode", "")
                                                        or addr_dict.get("Postcode", ""))
                            result["phone"] = _clean(addr_dict.get("phone", "")
                                                     or addr_dict.get("Phone", ""))
                            result["mobile"] = _clean(addr_dict.get("mobile", "")
                                                      or addr_dict.get("Mobile", ""))
                    except Exception as e:
                        result["address_error"] = str(e)

                # Step 4: Get line items from DocketLine
                try:
                    cursor.execute(
                        "SELECT * FROM DocketLine WHERE docket_id = ?",
                        (int(invoice),))
                    lines = cursor.fetchall()
                    if lines:
                        cols = [col[0] for col in cursor.description]
                        items = []
                        for line in lines:
                            ld = dict(zip(cols, line))
                            desc = _clean(ld.get("description", "")
                                         or ld.get("Description", ""))
                            qty = ld.get("quantity", "") or ld.get("Quantity", "")
                            if desc:
                                items.append(f"{qty} x {desc}" if qty else desc)
                        result["products"] = "\n".join(items)
                        result["line_count"] = len(items)
                except Exception as e:
                    result["lines_error"] = str(e)

                # Step 5: Get payment info from DocketPayments
                try:
                    cursor.execute(
                        "SELECT * FROM DocketPayments WHERE docket_id = ?",
                        (int(invoice),))
                    payments = cursor.fetchall()
                    if payments:
                        cols = [col[0] for col in cursor.description]
                        pay_types = []
                        total = 0
                        for p in payments:
                            pd = dict(zip(cols, p))
                            pt = _clean(pd.get("paymenttype", "")
                                       or pd.get("PaymentType", ""))
                            amt = pd.get("amount", 0) or pd.get("Amount", 0)
                            if pt:
                                pay_types.append(pt)
                            if amt:
                                total += float(amt)
                        result["payment_type"] = ", ".join(pay_types) if pay_types else None
                        result["total"] = round(total, 2)
                except Exception as e:
                    result["payment_error"] = str(e)

            else:
                result["docket_found"] = False
                result["message"] = f"Invoice {invoice} not found"

        except Exception as e:
            result["docket_found"] = False
            result["error"] = str(e)

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══ CUSTOMER SEARCH ═══
@app.route("/customer")
def search_customer():
    """
    Search customers by name (partial match).
    Query: GET /customer?q=smith
    Returns up to 20 matching customers.
    """
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"error": "Search query too short (min 2 chars)"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        # Search in Customer table — name1 and name2 fields
        cursor.execute("""
            SELECT TOP 20 * FROM Customer
            WHERE name1 LIKE ? OR name2 LIKE ?
        """, (f"%{q}%", f"%{q}%"))

        results = []
        cols = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            results.append({
                "customer_id": str(d.get("customer_id", "")),
                "name1": _clean(d.get("name1", "") or d.get("Name1", "")),
                "name2": _clean(d.get("name2", "") or d.get("Name2", "")),
            })

        conn.close()
        return jsonify({"results": results, "count": len(results)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══ DEBUG: LIST TABLES ═══
@app.route("/tables")
def list_tables():
    """List all tables in the database. Useful for exploring the schema."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        tables = []
        for row in cursor.tables(tableType="TABLE"):
            tables.append(row.table_name)
        conn.close()
        return jsonify({"tables": sorted(tables)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══ DEBUG: LIST COLUMNS ═══
@app.route("/columns")
def list_columns():
    """List columns in a specific table. GET /columns?table=Customer"""
    table = request.args.get("table", "")
    if not table:
        return jsonify({"error": "Specify ?table=TableName"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cols = []
        for row in cursor.columns(table=table):
            cols.append({
                "name": row.column_name,
                "type": row.type_name,
                "size": row.column_size
            })
        conn.close()
        return jsonify({"table": table, "columns": cols})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _clean(val):
    """Clean a value from the database — strip whitespace, convert to string."""
    if val is None:
        return None
    return str(val).strip() or None


# ═══ START SERVER ═══
if __name__ == "__main__":
    print("=" * 60)
    print("  NUWAY — Retail Manager Lookup API")
    print("=" * 60)

    # Resolve database path (from Supabase or local config)
    resolve_mdb_path()

    print(f"  Database: {MDB_PATH}")
    print(f"  Server:   http://{HOST}:{PORT}")
    print()

    if not os.path.exists(MDB_PATH):
        print(f"  ⚠ WARNING: Database file not found!")
        print(f"    Edit MDB_PATH in this script to point to your recent.mdb")
        print()

    print("  Endpoints:")
    print(f"    http://localhost:{PORT}/health")
    print(f"    http://localhost:{PORT}/lookup?invoice=41080")
    print(f"    http://localhost:{PORT}/customer?q=smith")
    print(f"    http://localhost:{PORT}/tables")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    app.run(host=HOST, port=PORT, debug=False)
