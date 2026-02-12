#!/usr/bin/env python3
"""Fix genetic_sequence schema to match router expectations."""
import psycopg2
import sys

DB_HOST = "192.168.0.189"
DB_PORT = 5432
DB_NAME = "mindex"
DB_USER = "mycosoft"
DB_PASSWORD = "mycosoft_mindex_2026"

FIXES = """
BEGIN;

-- Add missing 'gene' column (was 'gene_name')
ALTER TABLE bio.genetic_sequence ADD COLUMN IF NOT EXISTS gene VARCHAR(100);

-- Copy data from gene_name to gene if exists
UPDATE bio.genetic_sequence SET gene = gene_name WHERE gene IS NULL AND gene_name IS NOT NULL;

-- Add missing 'region' column
ALTER TABLE bio.genetic_sequence ADD COLUMN IF NOT EXISTS region VARCHAR(100);

-- Add indexes for new columns
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_gene ON bio.genetic_sequence (gene);
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_region ON bio.genetic_sequence (region);
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_gene_trgm ON bio.genetic_sequence USING gin (gene gin_trgm_ops);

COMMIT;
"""

def main():
    try:
        print("[*] Connecting to MINDEX DB...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=15
        )
        
        print("[*] Applying schema fixes...")
        cur = conn.cursor()
        cur.execute(FIXES)
        conn.commit()
        
        # Verify
        print("[*] Verifying columns...")
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'bio' AND table_name = 'genetic_sequence'
            AND column_name IN ('gene', 'region')
            ORDER BY column_name
        """)
        cols = cur.fetchall()
        print(f"[+] Found columns: {[c[0] for c in cols]}")
        
        cur.close()
        conn.close()
        
        print("[+] Schema fixed successfully")
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
