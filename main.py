from fastmcp import FastMCP
import os
import sqlite3
from datetime import date as dt_date

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker")

# ─────────────────────────────────────────────
# DB INIT
# ─────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as c:
        # Core expenses table
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                category    TEXT    NOT NULL,
                subcategory TEXT    DEFAULT '',
                note        TEXT    DEFAULT ''
            )
        """)

        # Budgets: one row per category per month (YYYY-MM)
        c.execute("""
            CREATE TABLE IF NOT EXISTS budgets(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                month       TEXT    NOT NULL,   -- e.g. "2025-06"
                category    TEXT    NOT NULL,
                limit_amt   REAL    NOT NULL,
                UNIQUE(month, category)
            )
        """)

        # Payments: outgoing payments you made (bills, EMIs, transfers)
        c.execute("""
            CREATE TABLE IF NOT EXISTS payments(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                payee       TEXT    NOT NULL,   -- who you paid
                method      TEXT    DEFAULT '', -- UPI / cash / card / NEFT
                reference   TEXT    DEFAULT '', -- transaction ID / UTR
                note        TEXT    DEFAULT ''
            )
        """)

init_db()


# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ═════════════════════════════════════════════
# EXPENSE TOOLS
# ═════════════════════════════════════════════

@mcp.tool()
def add_expense(date: str, amount: float, category: str,
                subcategory: str = "", note: str = "") -> dict:
    """Add a new expense entry to the database.

    Args:
        date: ISO date string, e.g. "2025-06-15"
        amount: Amount spent in INR
        category: Expense category, e.g. "Education"
        subcategory: Optional finer label, e.g. "Books"
        note: Any extra note
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
            (date, amount, category, subcategory, note)
        )
        return {"status": "ok", "id": cur.lastrowid}


@mcp.tool()
def edit_expense(expense_id: int, date: str = None, amount: float = None,
                 category: str = None, subcategory: str = None, note: str = None) -> dict:
    """Edit an existing expense by its ID. Only pass the fields you want to change.

    Args:
        expense_id: The ID of the expense to update
        date: New date (optional)
        amount: New amount (optional)
        category: New category (optional)
        subcategory: New subcategory (optional)
        note: New note (optional)
    """
    fields, params = [], []
    if date        is not None: fields.append("date=?");        params.append(date)
    if amount      is not None: fields.append("amount=?");      params.append(amount)
    if category    is not None: fields.append("category=?");    params.append(category)
    if subcategory is not None: fields.append("subcategory=?"); params.append(subcategory)
    if note        is not None: fields.append("note=?");        params.append(note)

    if not fields:
        return {"status": "error", "message": "No fields provided to update."}

    params.append(expense_id)
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(f"UPDATE expenses SET {', '.join(fields)} WHERE id=?", params)
        if cur.rowcount == 0:
            return {"status": "error", "message": f"No expense found with id={expense_id}"}
        return {"status": "ok", "updated_id": expense_id}


@mcp.tool()
def delete_expenses(expense_id: int = None, category: str = None,
                    amount: float = None, start_date: str = None,
                    end_date: str = None, note_contains: str = None) -> dict:
    """Delete expenses using flexible filters. All filters are AND-combined.

    Examples:
      - Delete a single entry       → expense_id=42
      - Delete all Education spends → category="Education"
      - Delete a ₹500 Education row → category="Education", amount=500
      - Delete entries in a range   → start_date="2025-06-01", end_date="2025-06-30"

    Args:
        expense_id: Exact row ID to delete (most precise)
        category: Filter by category name (case-insensitive)
        amount: Filter by exact amount
        start_date: Lower bound date (inclusive), e.g. "2025-06-01"
        end_date: Upper bound date (inclusive), e.g. "2025-06-30"
        note_contains: Delete only rows whose note contains this substring
    """
    if not any([expense_id, category, amount, start_date, end_date, note_contains]):
        return {"status": "error",
                "message": "Provide at least one filter to prevent accidental full deletion."}

    conditions, params = [], []

    if expense_id    is not None:
        conditions.append("id=?");                      params.append(expense_id)
    if category      is not None:
        conditions.append("LOWER(category)=LOWER(?)");  params.append(category)
    if amount        is not None:
        conditions.append("amount=?");                  params.append(amount)
    if start_date    is not None:
        conditions.append("date>=?");                   params.append(start_date)
    if end_date      is not None:
        conditions.append("date<=?");                   params.append(end_date)
    if note_contains is not None:
        conditions.append("note LIKE ?");               params.append(f"%{note_contains}%")

    where = " AND ".join(conditions)

    with sqlite3.connect(DB_PATH) as c:
        # First show what will be deleted
        preview = _rows(c.execute(f"SELECT * FROM expenses WHERE {where}", params))
        if not preview:
            return {"status": "not_found", "message": "No matching expenses found."}

        c.execute(f"DELETE FROM expenses WHERE {where}", params)
        return {"status": "ok", "deleted_count": len(preview), "deleted_rows": preview}


@mcp.tool()
def list_expenses(start_date: str, end_date: str) -> list[dict]:
    """List expense entries within an inclusive date range."""
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "SELECT id, date, amount, category, subcategory, note "
            "FROM expenses WHERE date BETWEEN ? AND ? ORDER BY date ASC, id ASC",
            (start_date, end_date)
        )
        return _rows(cur)


@mcp.tool()
def summarize(start_date: str, end_date: str, category: str = None) -> list[dict]:
    """Summarize expenses by category within an inclusive date range."""
    with sqlite3.connect(DB_PATH) as c:
        query = ("SELECT category, SUM(amount) AS total_amount "
                 "FROM expenses WHERE date BETWEEN ? AND ?")
        params = [start_date, end_date]
        if category:
            query += " AND LOWER(category)=LOWER(?)"
            params.append(category)
        query += " GROUP BY category ORDER BY total_amount DESC"
        return _rows(c.execute(query, params))


# ═════════════════════════════════════════════
# BUDGET TOOLS
# ═════════════════════════════════════════════

@mcp.tool()
def set_budget(month: str, category: str, limit_amt: float) -> dict:
    """Set or update a monthly spending budget for a category.

    Args:
        month: Month in YYYY-MM format, e.g. "2025-06"
        category: Category name, e.g. "Food"
        limit_amt: Budget ceiling in INR
    """
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO budgets(month, category, limit_amt) VALUES (?,?,?) "
            "ON CONFLICT(month, category) DO UPDATE SET limit_amt=excluded.limit_amt",
            (month, category, limit_amt)
        )
        return {"status": "ok", "month": month, "category": category, "limit": limit_amt}


@mcp.tool()
def check_budget(month: str, category: str = None) -> list[dict]:
    """Check budget vs actual spending for a month.
    Returns each category with its limit, amount spent, remaining, and a status flag.

    Args:
        month: Month in YYYY-MM format, e.g. "2025-06"
        category: Filter to a single category (optional)
    """
    start = f"{month}-01"
    end   = f"{month}-31"   # SQLite BETWEEN on TEXT is fine here

    with sqlite3.connect(DB_PATH) as c:
        query = ("SELECT b.category, b.limit_amt, "
                 "COALESCE(SUM(e.amount),0) AS spent "
                 "FROM budgets b "
                 "LEFT JOIN expenses e "
                 "  ON LOWER(e.category)=LOWER(b.category) AND e.date BETWEEN ? AND ? "
                 "WHERE b.month=?")
        params = [start, end, month]
        if category:
            query += " AND LOWER(b.category)=LOWER(?)"
            params.append(category)
        query += " GROUP BY b.category ORDER BY b.category"

        rows = _rows(c.execute(query, params))

    result = []
    for r in rows:
        remaining = r["limit_amt"] - r["spent"]
        result.append({
            **r,
            "remaining": round(remaining, 2),
            "status": "over_budget" if remaining < 0
                      else "near_limit" if remaining < r["limit_amt"] * 0.1
                      else "ok"
        })
    return result


@mcp.tool()
def list_budgets(month: str = None) -> list[dict]:
    """List all budget entries. Optionally filter by month (YYYY-MM)."""
    with sqlite3.connect(DB_PATH) as c:
        if month:
            cur = c.execute(
                "SELECT * FROM budgets WHERE month=? ORDER BY month, category", (month,))
        else:
            cur = c.execute("SELECT * FROM budgets ORDER BY month, category")
        return _rows(cur)



# ═════════════════════════════════════════════
# PAYMENT TOOLS  (outgoing payments you made)
# ═════════════════════════════════════════════

@mcp.tool()
def make_payment(date: str, amount: float, payee: str,
                 method: str = "", reference: str = "", note: str = "") -> dict:
    """Record an outgoing payment you made (bill, EMI, transfer, etc.).

    Args:
        date: Payment date, e.g. "2025-06-15"
        amount: Amount paid in INR
        payee: Who you paid, e.g. "HDFC Bank EMI", "Electricity Board"
        method: Payment mode — UPI / Cash / Card / NEFT / IMPS
        reference: Transaction ID, UTR number, or cheque number
        note: Any additional note
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO payments(date, amount, payee, method, reference, note) VALUES (?,?,?,?,?,?)",
            (date, amount, payee, method, reference, note)
        )
        return {"status": "ok", "id": cur.lastrowid}


@mcp.tool()
def list_payments(start_date: str, end_date: str,
                  payee: str = None, method: str = None) -> list[dict]:
    """List payments within a date range, optionally filtered by payee or method.

    Args:
        start_date: Start date inclusive, e.g. "2025-06-01"
        end_date: End date inclusive, e.g. "2025-06-30"
        payee: Filter by payee name (optional)
        method: Filter by payment method (optional)
    """
    conditions = ["date BETWEEN ? AND ?"]
    params     = [start_date, end_date]

    if payee:  conditions.append("LOWER(payee)=LOWER(?)");   params.append(payee)
    if method: conditions.append("LOWER(method)=LOWER(?)");  params.append(method)

    where = " AND ".join(conditions)
    with sqlite3.connect(DB_PATH) as c:
        return _rows(c.execute(
            f"SELECT * FROM payments WHERE {where} ORDER BY date ASC, id ASC", params))


@mcp.tool()
def payment_summary(start_date: str, end_date: str) -> dict:
    """Get total payments made, broken down by payee and method, within a date range.

    Args:
        start_date: Start date inclusive
        end_date: End date inclusive
    """
    with sqlite3.connect(DB_PATH) as c:
        total = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE date BETWEEN ? AND ?",
            (start_date, end_date)
        ).fetchone()[0]

        by_payee = _rows(c.execute(
            "SELECT payee, SUM(amount) AS total FROM payments "
            "WHERE date BETWEEN ? AND ? GROUP BY payee ORDER BY total DESC",
            (start_date, end_date)
        ))

        by_method = _rows(c.execute(
            "SELECT method, SUM(amount) AS total FROM payments "
            "WHERE date BETWEEN ? AND ? GROUP BY method ORDER BY total DESC",
            (start_date, end_date)
        ))

    return {"total_paid": round(total, 2),
            "by_payee": by_payee,
            "by_method": by_method}


# ─────────────────────────────────────────────
# RESOURCE
# ─────────────────────────────────────────────

@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()




if __name__== "_main_":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
# mcp.run()