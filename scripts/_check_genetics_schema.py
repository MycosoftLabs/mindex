#!/usr/bin/env python3
"""Check actual genetic_sequence table schema."""
import psycopg2
import sys

DB_HOST = "192.168.0.189"
DB_PORT = 5432
DB_NAME = "mindex"
DB_USER = "mycosoft"
DB_PASSWORD = "mycosoft_mindex_2026"

def main():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=15
        )
        cur = conn.cursor()
        
        # Get actual column list
        print("[*] Checking bio.genetic_sequence columns...")
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'bio' 
            AND table_name = 'genetic_sequence'
            ORDER BY ordinal_position
        """)
        
        columns = cur.fetchall()
        print(f"Found {len(columns)} columns:")
        for col_name, col_type in columns:
            print(f"  - {col_name}: {col_type}")
        
        # Check if we need to add missing columns
        existing_cols = {c[0] for c in columns}
        required_cols = {
            'id', 'accession', 'species_name', 'gene', 'region',
            'sequence', 'sequence_length', 'sequence_type', 'source'
        }
        
        missing = required_cols - existing_cols
        if missing:
            print(f"\n[!] Missing columns: {missing}")
            print("[*] These need to be added via ALTER TABLE")
        else:
            print("\n[+] All required columns present")
        
        cur.close()
        conn.close()
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
