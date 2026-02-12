"""Tail ETL logs on VM 189."""
import os
import paramiko

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = os.environ.get("VM_PASSWORD", "Mushroom1!Mushroom1!")

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_IP, username=VM_USER, password=VM_PASSWORD)
    
    # Check last 30 lines of ETL log
    stdin, stdout, stderr = ssh.exec_command("tail -30 /home/mycosoft/mindex/etl.log")
    output = stdout.read().decode(errors='replace')
    
    print("ETL LOG (last 30 lines):")
    print("="*70)
    for line in output.split('\n'):
        # Truncate very long lines
        if len(line) > 150:
            print(line[:150] + "...")
        else:
            print(line)
    
    ssh.close()

if __name__ == "__main__":
    main()
