#!/usr/bin/env python3
import os
import paramiko

VM_PASS = os.environ.get("VM_PASSWORD", "")


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

    tests = [
        "http://localhost:8000/api/mindex/observations?limit=2",
        "http://localhost:8000/api/mindex/observations?bbox=-117.6,32.5,-116.0,33.5&limit=5",
        "http://localhost:8000/api/mindex/observations?bbox=-117.6,32.5,-116.0,33.5&kingdom=Fungi&limit=5",
        "http://localhost:8000/api/mindex/observations?kingdom=Fungi&limit=5",
    ]
    for url in tests:
        _, o, _ = ssh.exec_command(
            f"curl -s -w '\\nHTTP:%{{http_code}}' '{url}' -H 'X-API-Key: $MINDEX_API_KEY'",
            timeout=30,
        )
        print("===", url)
        print(o.read().decode()[:900])
        print()

    _, o, _ = ssh.exec_command("docker logs mindex-api --tail 50 2>&1", timeout=30)
    print("=== logs ===")
    print(o.read().decode()[-2500:])

    ssh.close()


if __name__ == "__main__":
    main()
