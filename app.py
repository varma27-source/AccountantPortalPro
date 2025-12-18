import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
import re
import shutil
import json
import pytesseract
from PIL import Image

# --- SECURITY IMPORTS ---
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_key'

# --- SECURITY: AUTO LOGOUT ---
# If user is inactive for 5 minutes, their session dies.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)

# POINT TO THE OCR ENGINE
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Configuration
UPLOAD_FOLDER = 'uploads'
RECEIPT_FOLDER = 'receipts'
ARCHIVE_FOLDER = 'archives'

for folder in [UPLOAD_FOLDER, RECEIPT_FOLDER, ARCHIVE_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# --- CLOUD DATABASE CONNECTION ---
# Your specific Neon.tech connection string
DATABASE_URL = "postgresql://neondb_owner:npg_Rx9cW0iMJNDo@ep-lingering-tooth-a1zbi3jg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- SECURITY SETUP ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- 1. CONFIGURATION: Categories & Tax ---
CATEGORY_RULES = {
    "Income": ["salary", "deposit", "credit", "allowance", "refund", "profit", "dividend", "duitnow", "cash in"], 
    "Food": ["mcdonalds", "kfc", "starbucks", "restaurant", "food", "cafe", "mamak", "burger", "familymart", "family mart", "coffee", "roti", "nasi"],
    "Transport": ["petronas", "shell", "grab", "uber", "caltex", "toll", "touchngo", "parking", "bhpetrol"],
    "Utilities": ["tnb", "air selangor", "telekom", "maxis", "digi", "celcom", "wifi", "water", "bill", "unifi"],
    "Shopping": ["shopee", "lazada", "aeon", "lotus", "zara", "uniqlo", "watson", "guardian", "tiktok", "99speedmart", "99 speedmart", "7eleven", "7-eleven", "7 eleven"],
    "Transfer": ["transfer", "ibis", "instant transfer", "jompay"],
}

TAX_KEYWORDS = {
    "Lifestyle": ["book", "bookstore", "wifi", "unifi", "maxis", "digi", "gym", "sport", "decathlon", "computer", "phone"],
    "Medical": ["clinic", "hospital", "doctor", "pharmacy", "watson", "guardian", "medical"],
    "Insurance": ["insurance", "takaful", "prudential", "aia", "great eastern"],
    "Education": ["university", "college", "tuition", "udemy", "coursera"]
}

# --- USER SYSTEM ---
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # CHANGED: ? -> %s
    cur.execute('SELECT * FROM users WHERE id = %s', (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user:
        return User(id=user['id'], username=user['username'])
    return None

# --- 3. HELPER FUNCTIONS ---
def check_tax_relief(description):
    desc = description.lower()
    for tax_type, keywords in TAX_KEYWORDS.items():
        if any(k in desc for k in keywords):
            return tax_type
    return "None"

def categorize_description(description):
    desc_lower = description.lower()
    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword in desc_lower:
                return category
    return "Uncategorized"

def ai_auditor(amount, category):
    thresholds = {"Food": 150, "Transport": 200, "Utilities": 400, "Shopping": 800}
    limit = thresholds.get(category, 1000) 
    return amount > limit

# --- 4. ROUTES ---

@app.route('/')
@login_required 
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # CHANGED: ? -> %s
    cur.execute('SELECT * FROM transactions WHERE user_id = %s ORDER BY date DESC', (current_user.id,))
    transactions = cur.fetchall()
    cur.close()
    conn.close()
    
    trans_list = [dict(row) for row in transactions]
    
    # 1. Calculate Totals
    total_income = sum(t['amount'] for t in trans_list if t['type'] == 'Credit')
    total_expense = sum(t['amount'] for t in trans_list if t['type'] == 'Debit')
    balance = total_income - total_expense

    # 2. Charts & Analysis
    df = pd.DataFrame(trans_list)
    top_names = {}
    chart_labels = []
    chart_values = []
    
    if not df.empty:
        top_names = df.groupby('description')['amount'].sum().sort_values(ascending=False).head(5).to_dict()
        expense_df = df[df['type'] == 'Debit']
        if not expense_df.empty:
            cat_totals = expense_df.groupby('category')['amount'].sum().to_dict()
            chart_labels = list(cat_totals.keys())
            chart_values = list(cat_totals.values())

    return render_template('index.html', transactions=trans_list, 
                           top_names=top_names, 
                           income=total_income, expense=total_expense, balance=balance,
                           user=current_user,
                           chart_labels=json.dumps(chart_labels),
                           chart_values=json.dumps(chart_values))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # CHANGED: ? -> %s
        cur.execute('SELECT * FROM users WHERE username = %s', (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            user_obj = User(id=user['id'], username=user['username'])
            
            # 1. Log in (remember=False -> Logout if browser closes)
            login_user(user_obj, remember=False)
            
            # 2. Start the 5-minute timer
            session.permanent = True 
            
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password')
        
        # 1. NEW: Get Security Details from the form
        security_question = request.form.get('security_question')
        security_answer = request.form.get('security_answer')

        # 2. Check if Passwords Match
        if password != confirm_password:
            flash('Passwords do not match! Please try again.')
            return redirect(url_for('register'))

        # 3. Hash Password AND Security Answer
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        # We normalize the answer (lowercase + remove spaces) so "Rover" matches "rover"
        hashed_answer = generate_password_hash(security_answer.lower().strip(), method='pbkdf2:sha256')
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 4. Save everything to Cloud (Updated Query)
            cur.execute('''INSERT INTO users 
                           (username, password, security_question, security_answer) 
                           VALUES (%s, %s, %s, %s)''', 
                           (username, hashed_pw, security_question, hashed_answer))
            conn.commit()
            flash('Account created! Please login.')
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('Username already exists.')
        except Exception as e:
            conn.rollback()
            flash(f'Error: {e}')
        finally:
            cur.close()
            conn.close()
            
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files: return redirect(url_for('index'))
    file = request.files['file']
    if file.filename == '': return redirect(url_for('index'))

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)
    
    new_data = parse_universal_statement(filepath)
    conn = get_db_connection()
    cur = conn.cursor()
    count = 0
    
    for row in new_data:
        # CHANGED: ? -> %s
        cur.execute('SELECT 1 FROM transactions WHERE id = %s', (row['id'],))
        exists = cur.fetchone()
        
        if not exists:
            # We add 'user_id' to the columns and 'current_user.id' to the values
            # CHANGED: ? -> %s
            cur.execute('''INSERT INTO transactions 
                            (id, date, description, amount, type, category, tax_category, receipt_path, status, user_id) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', 
                            (row['id'], row['date'], row['description'], row['amount'], 
                             row['type'], row['category'], row['tax_category'], 
                             None, row['status'], current_user.id))
            count += 1
            
    conn.commit()
    cur.close()
    conn.close()
    flash(f'Success! Added {count} new transactions.')
    return redirect(url_for('index'))

@app.route('/upload_receipt/<id>', methods=['POST'])
@login_required
def upload_receipt(id):
    file = request.files['receipt']
    if file:
        filename = f"receipt_{id}_{file.filename}"
        save_path = os.path.join(RECEIPT_FOLDER, filename)
        file.save(save_path)
        
        conn = get_db_connection()
        cur = conn.cursor()
        # CHANGED: ? -> %s
        cur.execute('UPDATE transactions SET receipt_path = %s WHERE id = %s', (filename, id))
        conn.commit()
        cur.close()
        conn.close()
        flash("Receipt uploaded!")
    return redirect(url_for('index'))

@app.route('/search')
@login_required
def search():
    query = request.args.get('query', '').lower()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    if query:
        # CHANGED: ? -> %s
        sql = "SELECT * FROM transactions WHERE (lower(description) LIKE %s OR lower(category) LIKE %s) AND user_id = %s"
        cur.execute(sql, (f'%{query}%', f'%{query}%', current_user.id))
        transactions = cur.fetchall()
    else:
        # CHANGED: Filter by user_id
        cur.execute('SELECT * FROM transactions WHERE user_id = %s', (current_user.id,))
        transactions = cur.fetchall()
        
    cur.close()
    conn.close()
    return render_template('transaction_list.html', transactions=[dict(row) for row in transactions])

@app.route('/archive_reset')
@login_required
def archive_reset():
    # Only deletes current user's data in the cloud
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM transactions WHERE user_id = %s', (current_user.id,))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"System Reset! Your data has been cleared from the cloud.")
    return redirect(url_for('index'))

@app.route('/export')
@login_required
def export_data():
    conn = get_db_connection()
    # Filter by user_id
    df = pd.read_sql_query("SELECT * FROM transactions WHERE user_id = %s", conn, params=(current_user.id,))
    conn.close()
    export_path = os.path.join(UPLOAD_FOLDER, 'My_Accounts.xlsx')
    df.to_excel(export_path, index=False)
    return send_file(export_path, as_attachment=True)

# --- 5. CHATBOT LOGIC ---
@app.route('/chat', methods=['POST'])
@login_required
def chat():
    user_msg = request.json.get('message', '').lower()
    conn = get_db_connection()
    # Filter by user_id
    df = pd.read_sql_query("SELECT * FROM transactions WHERE user_id = %s", conn, params=(current_user.id,))
    conn.close()
    
    if df.empty: return jsonify({"response": "No data available yet."})

    aliases = { "7e": "7-eleven", "mcd": "mcdonalds", "starbies": "starbucks", "food": ["food", "mcd", "kfc", "restaurant"], "transport": ["grab", "petrol", "toll", "parking"] }
    sum_keywords = ["total", "how much", "sum", "spent", "cost"]
    wants_sum = any(word in user_msg for word in sum_keywords)

    filtered_df = df.copy()
    search_term = None
    words = user_msg.split()
    clean_words = [w for w in words if w not in sum_keywords and w not in ["show", "me", "for", "the", "all", "is", "in"]]

    if clean_words:
        raw_term = clean_words[-1] 
        search_term = aliases.get(raw_term, raw_term) 
        if isinstance(search_term, list):
            mask = filtered_df['description'].str.lower().str.contains('|'.join(search_term)) | filtered_df['category'].str.lower().isin([raw_term.capitalize()])
            filtered_df = filtered_df[mask]
        else:
            mask = filtered_df['description'].str.lower().str.contains(search_term) | filtered_df['category'].str.lower().str.contains(search_term)
            filtered_df = filtered_df[mask]

    if filtered_df.empty: return jsonify({"response": f"I couldn't find any transactions for '{search_term or 'that'}'."})

    total_amount = filtered_df['amount'].sum()
    count = len(filtered_df)
    if wants_sum:
        return jsonify({"response": f"You have spent a total of <b>RM {total_amount:,.2f}</b> on {search_term or 'everything'} ({count} transactions)."})
    else:
        resp = f"Found {count} transactions for '{search_term or 'all'}'.<br><b>Total: RM {total_amount:,.2f}</b><br><br>"
        for _, row in filtered_df.head(5).iterrows():
            d = row['date'] if row['date'] else "-"
            resp += f"• {d}: {row['description']} - RM {row['amount']}<br>"
        if count > 5: resp += f"<i>...and {count - 5} more.</i>"
        return jsonify({"response": resp})

# --- 6. PARSER (UPDATED FOR GX BANK) ---
def parse_universal_statement(filepath):
    extracted_data = []
    # This matches dates like "1 Nov" or "1 Nov 2025" 
    date_pattern = re.compile(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)')

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            # Extract tables to find columns for "Money in" and "Money out" 
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    # Based on your GXBank PDF: row[0]=Date, row[1]=Description, row[2]=Money In, row[3]=Money Out 
                    
                    # 1. Check if this is a transaction row (must have a valid date)
                    if not row[0] or not date_pattern.search(row[0]):
                        continue
                    
                    raw_date = date_pattern.search(row[0]).group(0)
                    description = row[1].replace('\n', ' ').strip() if row[1] else "No Description"
                    
                    # 2. Handle Money In vs Money Out columns separately
                    money_in_raw = row[2] if len(row) > 2 else None
                    money_out_raw = row[3] if len(row) > 3 else None
                    
                    amount = 0.0
                    trans_type = "Debit" # Default to expense

                    # Check "Money In" column (Credits)
                    if money_in_raw and any(char.isdigit() for char in money_in_raw):
                        clean_val = money_in_raw.replace('+', '').replace('RM', '').replace(',', '').strip()
                        amount = float(clean_val)
                        trans_type = "Credit"
                    
                    # Check "Money Out" column (Debits)
                    elif money_out_raw and any(char.isdigit() for char in money_out_raw):
                        clean_val = money_out_raw.replace('-', '').replace('RM', '').replace(',', '').strip()
                        amount = float(clean_val)
                        trans_type = "Debit"

                    # 3. Use your existing categorization logic
                    category = categorize_description(description)
                    if trans_type == "Credit":
                        category = "Income" # Ensure "Money In" is always categorized as Income
                    
                    tax_cat = check_tax_relief(description)
                    status = "High Spend" if ai_auditor(amount, category) else "Verified"

                    # 4. Clean up the date for the database
                    try:
                        clean_date = datetime.strptime(f"{raw_date} {datetime.now().year}", '%d %b %Y')
                    except:
                        clean_date = datetime.now()

                    extracted_data.append({
                        'id': datetime.now().strftime('%Y%m%d%H%M%S') + str(len(extracted_data)),
                        'date': clean_date.strftime('%Y-%m-%d'),
                        'description': description,
                        'amount': amount,
                        'type': trans_type,
                        'category': category,
                        'tax_category': tax_cat,
                        'status': status
                    })
    return extracted_data# --- 6. PARSER (MEMORY OPTIMIZED FOR LARGE STATEMENTS) ---
def parse_universal_statement(filepath):
    extracted_data = []
    # This matches dates like "1 Nov" or "1 Nov 2025" 
    date_pattern = re.compile(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)')

    with pdfplumber.open(filepath) as pdf:
        # Process one page at a time to stay within Render's RAM limits
        for page in pdf.pages:
            tables = page.extract_tables()
            
            if not tables:
                page.flush_cache() # Clear RAM for this page
                continue
                
            for table in tables:
                for row in table:
                    # Validate row structure (GXBank: 0=Date, 1=Desc, 2=In, 3=Out)
                    if not row or len(row) < 4 or not row[0]:
                        continue
                    
                    # Search for date in first column
                    date_text = str(row[0])
                    date_match = date_pattern.search(date_text)
                    if not date_match:
                        continue
                    
                    raw_date = date_match.group(0)
                    description = str(row[1]).replace('\n', ' ').strip() if row[1] else "No Description"
                    
                    # Handle Money In vs Money Out columns
                    money_in_raw = str(row[2]) if row[2] else ""
                    money_out_raw = str(row[3]) if row[3] else ""
                    
                    amount = 0.0
                    trans_type = "Debit" # Default

                    # 1. Logic for Money In (Credits/Income)
                    # We check for '+' or if the In column has data while Out is empty
                    if '+' in money_in_raw or (money_in_raw.strip() and not money_out_raw.strip()):
                        try:
                            clean_val = money_in_raw.replace('+', '').replace('RM', '').replace(',', '').strip()
                            amount = float(clean_val)
                            trans_type = "Credit"
                        except: continue
                    
                    # 2. Logic for Money Out (Debits/Expenses)
                    elif '-' in money_out_raw or money_out_raw.strip():
                        try:
                            clean_val = money_out_raw.replace('-', '').replace('RM', '').replace(',', '').strip()
                            amount = float(clean_val)
                            trans_type = "Debit"
                        except: continue

                    # 3. Categorization
                    category = categorize_description(description)
                    if trans_type == "Credit":
                        category = "Income" # Force all Money In to Income category
                    
                    tax_cat = check_tax_relief(description)
                    status = "High Spend" if ai_auditor(amount, category) else "Verified"

                    # 4. Clean Date
                    try:
                        clean_date = datetime.strptime(f"{raw_date} {datetime.now().year}", '%d %b %Y')
                    except:
                        clean_date = datetime.now()

                    extracted_data.append({
                        'id': datetime.now().strftime('%Y%m%d%H%M%S') + str(len(extracted_data)),
                        'date': clean_date.strftime('%Y-%m-%d'),
                        'description': description,
                        'amount': amount,
                        'type': trans_type,
                        'category': category,
                        'tax_category': tax_cat,
                        'status': status
                    })
            
            # CRITICAL: Clear the page cache to free up RAM immediately
            page.flush_cache()
            
    return extracted_data

@app.route('/scan_receipt', methods=['POST'])
@login_required
def scan_receipt():
    if 'receipt_image' not in request.files: return jsonify({"error": "No image uploaded"}), 400
    file = request.files['receipt_image']
    if file.filename == '': return jsonify({"error": "No selected file"}), 400
    img_path = os.path.join(RECEIPT_FOLDER, "temp_scan.jpg")
    file.save(img_path)
    try:
        text = pytesseract.image_to_string(Image.open(img_path))
        date_match = re.search(r'(\d{1,2}[/-]\d{2}[/-]\d{2,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))', text, re.IGNORECASE)
        amount_matches = re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)
        detected_date = date_match.group(0) if date_match else datetime.now().strftime('%Y-%m-%d')
        detected_amount = max([float(a.replace(',', '')) for a in amount_matches]) if amount_matches else 0.0
        return jsonify({"success": True, "text": text[:200], "date": detected_date, "amount": detected_amount})
    except Exception as e: return jsonify({"error": str(e)}), 500

# --- 7. NEW API: SAVE SCANNED DATA TO DB ---
@app.route('/save_scan', methods=['POST'])
@login_required
def save_scan():
    data = request.json
    date = data.get('date')
    amount = data.get('amount')
    text = data.get('text')

    clean_desc = text.replace('\n', ' ')[:30] + "..." if text else "Scanned Receipt"
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        new_id = datetime.now().strftime('%Y%m%d%H%M%S') 
        # CHANGED: ? -> %s
        cur.execute('''INSERT INTO transactions 
                        (id, date, description, amount, type, category, tax_category, receipt_path, status, user_id) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', 
                        (new_id, date, clean_desc, amount, 'Debit', 'Uncategorized', 'None', None, 'Verified', current_user.id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})
    
# --- 8. FORGOT PASSWORD LOGIC ---
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    # STAGE 2: Verify Answer & Reset Password
    if request.method == 'POST' and 'security_answer' in request.form:
        username = request.form['username']
        answer = request.form['security_answer']
        new_pw = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Find the user again to check the answer
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        
        # Check if the answer matches the hashed answer in DB
        if user and check_password_hash(user['security_answer'], answer.lower().strip()):
            # SUCCESS: Update the password!
            hashed_pw = generate_password_hash(new_pw, method='pbkdf2:sha256')
            cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_pw, user['id']))
            conn.commit()
            cur.close()
            conn.close()
            flash('✅ Password reset successful! You can now login.')
            return redirect(url_for('login'))
        else:
            # FAIL: Wrong answer
            cur.close()
            conn.close()
            flash('❌ Incorrect security answer.')
            # Send them back to try again
            return render_template('forgot_password.html', stage='verify_answer', question=user['security_question'], username=username)

    # STAGE 1: Find User & Get Question
    elif request.method == 'POST':
        username = request.form['username']
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and user.get('security_question'):
            # Found user! Show them the question.
            return render_template('forgot_password.html', stage='verify_answer', question=user['security_question'], username=username)
        else:
            flash('User not found or no security question set.')
            return redirect(url_for('forgot_password'))

    # INITIAL LOAD: Show simple form
    return render_template('forgot_password.html', stage='find_user')

# --- 9. ACCOUNT SETTINGS ROUTES ---

@app.route('/settings')
@login_required
def settings():
    # Simply renders the settings page
    return render_template('settings.html')

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    current_password = request.form['current_password']
    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id = %s", (current_user.id,))
    user = cur.fetchone()
    cur.close()
    
    # 1. Check if the current password is correct
    if not check_password_hash(user['password'], current_password):
        conn.close()
        flash('alert-error', 'Current password is not correct.')
        return redirect(url_for('settings'))

    # 2. Check if new passwords match
    if new_password != confirm_password:
        conn.close()
        flash('alert-error', 'New password and confirmation do not match.')
        return redirect(url_for('settings'))

    # 3. Update the password in the database
    hashed_pw = generate_password_hash(new_password, method='pbkdf2:sha256')
    cur = conn.cursor()
    cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_pw, current_user.id))
    conn.commit()
    conn.close()
    
    flash('alert-success', 'Password updated successfully! Please re-login.')
    logout_user()
    return redirect(url_for('login'))

@app.route('/update_security', methods=['POST'])
@login_required
def update_security():
    verify_password = request.form['verify_password']
    new_question = request.form['security_question']
    new_answer = request.form['security_answer']

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id = %s", (current_user.id,))
    user = cur.fetchone()
    cur.close()

    # 1. Check current password for high-security operation
    if not check_password_hash(user['password'], verify_password):
        conn.close()
        flash('alert-error', 'Incorrect current password. Cannot update security details.')
        return redirect(url_for('settings'))

    # 2. Hash the new answer securely
    hashed_answer = generate_password_hash(new_answer.lower().strip(), method='pbkdf2:sha256')

    # 3. Update security details
    cur = conn.cursor()
    cur.execute("UPDATE users SET security_question = %s, security_answer = %s WHERE id = %s", 
                (new_question, hashed_answer, current_user.id))
    conn.commit()
    conn.close()

    flash('alert-success', 'Security Question and Answer successfully updated.')
    return redirect(url_for('settings'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)