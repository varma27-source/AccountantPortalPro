import psycopg2

# Your Cloud Key
DB_URL = "postgresql://neondb_owner:npg_Rx9cW0iMJNDo@ep-lingering-tooth-a1zbi3jg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

def create_tables():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        print("1. Creating Users Table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL
            );
        """)
        
        print("2. Creating Transactions Table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                date TEXT,
                description TEXT,
                amount REAL,
                type TEXT,
                category TEXT,
                tax_category TEXT,
                receipt_path TEXT,
                status TEXT,
                user_id INTEGER
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ SUCCESS! Cloud Database is ready.")
        
    except Exception as e:
        print("❌ Error:", e)

if __name__ == "__main__":
    create_tables()