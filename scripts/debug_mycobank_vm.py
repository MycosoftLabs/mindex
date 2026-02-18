#!/usr/bin/env python3
"""
Debug MycoBank HTML on VM 189.

This script is intentionally read-only: it fetches a MycoBank search page from the VM
and prints a small snippet so we can fix the scraper selectors without guessing.
"""

from __future__ import annotations

import os

import paramiko


def main() -> int:
    vm_pass = os.environ.get("VM_PASSWORD")
    if not vm_pass:
        print("ERROR: VM_PASSWORD not set")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=vm_pass, timeout=30)

    html_url = "https://www.mycobank.org/Basic%20names%20search?Name=agaricus&page=1"
    api_url = "https://www.mycobank.org/Services/MycoBankNumberService.svc/json/SearchSpecies?Name=a%25&Start=0&Limit=5"
    remote = (
        "python3 -c \"import httpx; "
        "headers={'User-Agent':'Mozilla/5.0','Accept':'application/json, text/plain, */*','Accept-Language':'en-US,en;q=0.9','Referer':'https://www.mycobank.org/'}; "
        f"html_url='{html_url}'; "
        "r=httpx.get(html_url, follow_redirects=True, timeout=60, headers=headers); "
        "print('HTML status:', r.status_code); "
        "print('HTML len:', len(r.text)); "
        "print('HTML head:', r.text[:500].replace('\\n',' ') ); "
        "print('HTML tail:', r.text[-500:].replace('\\n',' ') ); "
        f"api_url='{api_url}'; "
        "r2=httpx.get(api_url, follow_redirects=True, timeout=60, headers=headers); "
        "print('API status:', r2.status_code); "
        "print('API content-type:', r2.headers.get('content-type')); "
        "print('API head:', r2.text[:800].replace('\\n',' ') ); "
        # Try to find underlying endpoints by inspecting the JS bundle
        "js_url='https://www.mycobank.org/main.de55b5a77d0f160d.js'; "
        "r3=httpx.get(js_url, follow_redirects=True, timeout=60, headers=headers); "
        "print('JS status:', r3.status_code); "
        "txt=r3.text; "
        "keys=['SearchSpecies','MycoBank','MycoBankNr','svc','/api','Services']; "
        "import re; "
        "print('JS contains map:', {k:(k in txt) for k in keys}); "
        "all_api=sorted(set(re.findall(r'/api/[A-Za-z0-9_\\-\\/]+', txt))); "
        "print('JS api paths count:', len(all_api)); "
        "hot=[p for p in all_api if any(k in p.lower() for k in ['name','tax','myco','fung','species','search'])][:80]; "
        "print('JS api paths filtered:', hot); "
        "idx=txt.find('SearchSpecies'); "
        "print('JS SearchSpecies snippet:', txt[max(0,idx-200):idx+200] if idx!=-1 else 'N/A');\""
    )

    stdin, stdout, stderr = ssh.exec_command(remote, timeout=90)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    print(out)
    if err.strip():
        print("STDERR:")
        print(err)

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

