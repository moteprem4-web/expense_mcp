import os
import sqlite3
import json
from fastmcp import FastMCP
from datetime import date as dt_date

# ─── CONFIGURATION ───────────────────────────────────────────
# Use an absolute path to ensure the DB is found regardless of how the script is run
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "expenses.db")
CATEGORIES_PATH = os.path.join(BASE_DIR, "categories.json")

mcp = FastMCP("ExpenseTracker")

# ─── DB INITIALIZATION ───────────────────────────────────────
def get_db_connection():
    """Create a thread-safe connection with WAL mode enabled."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # WAL mode allows multiple readers and one writer without locking
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    try:
        with get_db_connection() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS expenses(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT DEFAULT '',
                    note TEXT DEFAULT ''
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS budgets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    limit_amt REAL NOT NULL,
                    UNIQUE(month, category)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS payments(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    payee TEXT NOT NULL,
                    method TEXT DEFAULT '',
                    reference TEXT DEFAULT '',
                    note TEXT DEFAULT ''
                )
            """)
    except sqlite3.OperationalError as e:
        print(f"CRITICAL: Could not initialize DB. Is the directory read-only? {e}")

init_db()

# ─── HELPER ─────────────────────────────────────────────────
def _rows(cur) -> list[dict]:
    if not cur.description: return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

# ─── EXPENSE TOOLS ──────────────────────────────────────────

@mcp.tool()
def add_expense(date: str, amount: float, category: str, subcategory: str = "", note: str = "") -> dict:
    """Add a new expense entry."""
    try:
        with get_db_connection() as c:
            cur = c.execute(
                "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
                (date, amount, category, subcategory, note)
            )
            return {"status": "ok", "id": cur.lastrowid}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def edit_expense(expense_id: int, date: str = None, amount: float = None,
                 category: str = None, subcategory: str = None, note: str = None) -> dict:
    """Edit an existing expense by ID."""
    fields, params = [], []
    if date is not None: fields.append("date=?"); params.append(date)
    if amount is not None: fields.append("amount=?"); params.append(amount)
    if category is not None: fields.append("category=?"); params.append(category)
    if subcategory is not None: fields.append("subcategory=?"); params.append(subcategory)
    if note is not None: fields.append("note=?"); params.append(note)

    if not fields:
        return {"status": "error", "message": "No fields provided."}

    params.append(expense_id)
    try:
        with get_db_connection() as c:
            cur = c.execute(f"UPDATE expenses SET {', '.join(fields)} WHERE id=?", params)
            if cur.rowcount == 0:
                return {"status": "error", "message": f"No expense found with id={expense_id}"}
            return {"status": "ok", "updated_id": expense_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def delete_expenses(expense_id: int = None, category: str = None,
                    amount: float = None, start_date: str = None,
                    end_date: str = None, note_contains: str = None) -> dict:
    """Delete expenses using filters."""
    if not any([expense_id, category, amount, start_date, end_date, note_contains]):
        return {"status": "error", "message": "Provide at least one filter."}

    conditions, params = [], []
    if expense_id is not None: conditions.append("id=?"); params.append(expense_id)
    if category is not None: conditions.append("LOWER(category)=LOWER(?)"); params.append(category)
    if amount is not None: conditions.append("amount=?"); params.append(amount)
    if start_date is not None: conditions.append("date>=?"); params.append(start_date)
    if end_date is not None: conditions.append("date<=?"); params.append(end_date)
    if note_contains is not None: conditions.append("note LIKE ?"); params.append(f"%{note_contains}%")

    where = " AND ".join(conditions)
    try:
        with get_db_connection() as c:
            preview = _rows(c.execute(f"SELECT * FROM expenses WHERE {where}", params))
            if not preview: return {"status": "not_found", "message": "No matches."}
            c.execute(f"DELETE FROM expenses WHERE {where}", params)
            return {"status": "ok", "deleted_count": len(preview), "deleted_rows": preview}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def list_expenses(start_date: str, end_date: str) -> list[dict]:
    """List expenses in date range."""
    with get_db_connection() as c:
        cur = c.execute("SELECT * FROM expenses WHERE date BETWEEN ? AND ? ORDER BY date ASC", (start_date, end_date))
        return _rows(cur)

# ─── BUDGET TOOLS ───────────────────────────────────────────

@mcp.tool()
def set_budget(month: str, category: str, limit_amt: float) -> dict:
    """Set or update a monthly budget."""
    try:
        with get_db_connection() as c:
            c.execute(
                "INSERT INTO budgets(month, category, limit_amt) VALUES (?,?,?) "
                "ON CONFLICT(month, category) DO UPDATE SET limit_amt=excluded.limit_amt",
                (month, category, limit_amt)
            )
            return {"status": "ok", "month": month, "category": category, "limit": limit_amt}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def check_budget(month: str, category: str = None) -> list[dict]:
    """Check budget vs actual spending."""
    start, end = f"{month}-01", f"{month}-31"
    try:
        with get_db_connection() as c:
            query = ("SELECT b.category, b.limit_amt, COALESCE(SUM(e.amount),0) AS spent "
                     "FROM budgets b LEFT JOIN expenses e ON LOWER(e.category)=LOWER(b.category) "
                     "AND e.date BETWEEN ? AND ? WHERE b.month=?")
            params = [start, end, month]
            if category:
                query += " AND LOWER(b.category)=LOWER(?)"
                params.append(category)
            query += " GROUP BY b.category"
            rows = _rows(c.execute(query, params))
            
            for r in rows:
                r["remaining"] = round(r["limit_amt"] - r["spent"], 2)
                r["status"] = "over" if r["remaining"] < 0 else "ok"
            return rows
    except Exception as e:
        return [{"status": "error", "message": str(e)}]

# ─── RESOURCE ───────────────────────────────────────────────

@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    """Get the available categories list."""
    if not os.path.exists(CATEGORIES_PATH):
        return json.dumps(["Food", "Travel", "Bills", "Education", "Other"])
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()

# ─── RUN SERVER ─────────────────────────────────────────────

if __name__ == "__main__":
    # If deploying to a remote server, port 8000 and host 0.0.0.0 is standard
    # If running locally via stdio for a client, use mcp.run()
    mcp.run()