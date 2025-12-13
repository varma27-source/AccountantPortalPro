import psycopg2

# Your Neon Cloud Key
DB_URL = "postgresql://neondb_owner:npg_Rx9cW0iMJNDo@ep-lingering-tooth-a1zbi3jg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

def add_security_columns():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        print("⏳ Upgrading Users table...")
        
        # Add the two new columns
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_question TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_answer TEXT;")
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ SUCCESS! Database upgraded. You can now save security questions.")
        
    except Exception as e:
        print("❌ Error:", e)

if __name__ == "__main__":
    add_security_columns()