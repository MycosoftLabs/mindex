import paramiko
from pathlib import Path

pw = [l.split("=", 1)[1].strip() for l in Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local").read_text().splitlines() if l.startswith("VM_PASSWORD=")][0]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)

def run(cmd, timeout=120):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    ch = o.channel
    ch.settimeout(timeout)
    try:
        text = (o.read() + e.read()).decode(errors="replace")
    except Exception as ex:
        text = f"<timeout after {timeout}s: {ex}>"
    print(">", cmd)
    print(text[:4000])

run("sudo docker exec mindex-etl python -c \"from mindex_etl.jobs.sync_mycobank_taxa import sync_mycobank_taxa_compat; print(sync_mycobank_taxa_compat(max_pages=1))\"", timeout=90)
run('sudo docker exec mindex-postgres psql -U mindex -d mindex -tAc "SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;"')
run("sudo docker inspect mindex-etl --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'INAT_DOMAIN|GBIF_DOMAIN|LOCAL_DATA|max-pages' || true")
run("sudo docker logs mindex-etl --tail 40 2>&1 | grep -iE 'Job (mycobank|gbif|inat_taxa)|unexpected keyword|latitude|scrapes' || true")
run('set -a; . /home/mycosoft/mindex/.env; set +a; curl -s -H "X-API-Key: $MINDEX_API_KEY" "http://127.0.0.1:8000/api/mindex/taxa?q=Pleurotus&limit=3" | head -c 600')
c.close()
