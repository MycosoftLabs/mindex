"""Check ETL logs on VM 189."""
import os
import paramiko

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = os.environ.get("VM_PASSWORD", "Mushroom1!Mushroom1!")

def main():
    print("Connecting to VM 189...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_IP, username=VM_USER, password=VM_PASSWORD)
    print("Connected!")
    
    # Check ETL logs (last 100 lines)
    print("\n" + "="*70)
    print("ETL LOGS (last 100 lines):")
    print("="*70)
    stdin, stdout, stderr = ssh.exec_command("tail -100 /home/mycosoft/mindex/etl.log")
    output = stdout.read().decode(errors='ignore')
    print(output)
    
    # Check database counts
    print("\n" + "="*70)
    print("DATABASE COUNTS:")
    print("="*70)
    
    cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as taxon_count FROM core.taxon"'
    stdin, stdout, stderr = ssh.exec_command(cmd)
    print("Taxa:", stdout.read().decode(errors='ignore'))
    
    cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as observation_count FROM obs.observation"'
    stdin, stdout, stderr = ssh.exec_command(cmd)
    print("Observations:", stdout.read().decode(errors='ignore'))
    
    cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as compound_count FROM bio.compound"'
    stdin, stdout, stderr = ssh.exec_command(cmd)
    print("Compounds:", stdout.read().decode(errors='ignore'))
    
    cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as sequence_count FROM bio.genetic_sequence"'
    stdin, stdout, stderr = ssh.exec_command(cmd)
    print("Sequences:", stdout.read().decode(errors='ignore'))
    
    ssh.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
