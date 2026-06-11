"""SINE P0 ESC-50 training orchestration on MINDEX 189 (CPU, Legions offline) — JUN11_2026.

Steps (each gated on the previous):
 1. git pull + rebuild mindex-api image (installs CPU torch)
 2. write real-named ESC-50 meta csv on NAS
 3. train P0 TorchScript artifact inside container
 4. verify package -> register -> runtime smoke -> mark loaded
 5. build + register prototype catalog
 6. live E2E verifier

Run from MINDEX repo. Long-running (image build + CPU training).
"""
import os
import sys
from pathlib import Path

import paramiko

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _p(text: str) -> None:
    sys.stdout.write(text.encode("utf-8", "replace").decode("utf-8", "replace") + "\n")
    sys.stdout.flush()

for line in Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
PWD = os.environ.get("VM_PASSWORD") or os.environ["VM_SSH_PASSWORD"]

ESC50_NAMES = [
    "dog", "rooster", "pig", "cow", "frog", "cat", "hen", "insects_flying", "sheep", "crow",
    "rain", "sea_waves", "crackling_fire", "crickets", "chirping_birds", "water_drops", "wind",
    "pouring_water", "toilet_flush", "thunderstorm", "crying_baby", "sneezing", "clapping",
    "breathing", "coughing", "footsteps", "laughing", "brushing_teeth", "snoring",
    "drinking_sipping", "door_wood_knock", "mouse_click", "keyboard_typing", "door_wood_creaks",
    "can_opening", "washing_machine", "vacuum_cleaner", "clock_alarm", "clock_tick",
    "glass_breaking", "helicopter", "chainsaw", "siren", "car_horn", "engine", "train",
    "church_bells", "airplane", "fireworks", "hand_saw",
]

REMOTE = "/home/mycosoft/mindex"
PKG = "/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1"
AUDIO = "/mnt/nas/mindex/Library/acoustic/esc50"
CSV = f"{AUDIO}/meta/esc50.csv"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=PWD, timeout=30)


def run(label: str, cmd: str, t: int = 1800, quiet_ok: bool = False) -> str:
    _p(f"\n===== {label} =====")
    chan = ssh.get_transport().open_session()
    chan.settimeout(t)
    chan.exec_command(cmd)
    import time

    buf = b""
    deadline = time.time() + t
    while time.time() < deadline:
        if chan.recv_ready():
            buf += chan.recv(65536)
        if chan.exit_status_ready():
            while chan.recv_ready():
                buf += chan.recv(65536)
            break
        time.sleep(0.5)
    rc = chan.recv_exit_status() if chan.exit_status_ready() else -1
    out = buf.decode(errors="replace")
    _p(out[-2500:])
    _p(f"[rc={rc}]")
    return out


def step_build() -> None:
    run("git pull", f"cd {REMOTE} && git fetch origin && git reset --hard origin/main && git log -1 --oneline")
    run("build mindex-api (installs torch)", f"cd {REMOTE} && docker compose build api 2>&1 | tail -25", t=2400)
    run("up -d", f"cd {REMOTE} && docker compose up -d api && sleep 12 && curl -sf http://127.0.0.1:8000/api/mindex/health")
    out = run("torch in container", "docker exec mindex-api python3 -c 'import torch,soundfile; print(\"torch\",torch.__version__)' 2>&1")
    if "torch" not in out:
        sys.exit("torch not present after build")


def step_csv() -> None:
    names = ",".join(ESC50_NAMES)
    cmd = (
        f'python3 - "{AUDIO}" "{names}" <<"PY"\n'
        "import os,sys,glob\n"
        "audio=sys.argv[1]; names=sys.argv[2].split(',')\n"
        "os.makedirs(audio+'/meta',exist_ok=True)\n"
        "rows=['filename,fold,target,category,esc10,src_file,take']\n"
        "for f in sorted(glob.glob(audio+'/*.wav')):\n"
        "    b=os.path.basename(f); n=b[:-4]; parts=n.split('-')\n"
        "    if len(parts)<4: continue\n"
        "    fold=parts[0]; tgt=int(parts[-1]); cat=names[tgt] if 0<=tgt<len(names) else f'esc50_target_{tgt:02d}'\n"
        "    rows.append(f'{b},{fold},{tgt},{cat},False,{b},A')\n"
        "open(audio+'/meta/esc50.csv','w').write(chr(10).join(rows)+chr(10))\n"
        "print('wrote',len(rows)-1,'rows')\n"
        "PY"
    )
    run("write real-named esc50.csv", cmd, t=120)
    run("csv head", f"head -4 {CSV}; wc -l {CSV}")


def step_sync_scripts() -> None:
    run("git pull", f"cd {REMOTE} && git fetch origin && git reset --hard origin/main && git log -1 --oneline")
    run("docker cp fixed scripts into container", f"docker cp {REMOTE}/scripts/. mindex-api:/app/scripts/ && echo copied")


def step_train() -> None:
    step_sync_scripts()
    run(
        "train P0 (CPU)",
        f"docker exec mindex-api python3 scripts/train_sine_esc50_p0.py "
        f"--audio-root {AUDIO} --metadata-csv {CSV} --output-root /mnt/nas/mindex/models/acoustic "
        f"--epochs 12 --batch-size 32 --device cpu 2>&1 | tail -40",
        t=5400,
    )
    run("package listing", f"ls -la {PKG} 2>&1")


def step_register() -> None:
    run(
        "verify package",
        f"docker exec mindex-api python3 scripts/verify_sine_model_artifact_package.py "
        f"--package-root {PKG} --expected-model-id sine-esc50-cnn-p0-v1 "
        f"--write-report {PKG}/verification_report.json 2>&1 | tail -20",
        t=600,
    )
    run(
        "register artifact SQL",
        f"test -f {PKG}/register_model_artifact.sql && "
        f"docker exec -i mindex-postgres psql -U mindex -d mindex -v ON_ERROR_STOP=1 -f - "
        f"< {PKG}/register_model_artifact.sql 2>&1 | tail -8",
        t=300,
    )
    sample = run("pick esc50 clip", f"ls {AUDIO}/*.wav | head -1").strip().splitlines()[-1]
    run(
        "runtime smoke",
        f"docker exec mindex-api python3 scripts/smoke_sine_model_artifact_inference.py "
        f"--package-root {PKG} --wav-path {sample} --expected-model-id sine-esc50-cnn-p0-v1 "
        f"--write-report {PKG}/runtime_smoke_report.json --write-loaded-sql {PKG}/mark_model_loaded.sql 2>&1 | tail -20",
        t=600,
    )
    run(
        "mark loaded SQL",
        f"test -f {PKG}/mark_model_loaded.sql && "
        f"docker exec -i mindex-postgres psql -U mindex -d mindex -v ON_ERROR_STOP=1 -f - "
        f"< {PKG}/mark_model_loaded.sql 2>&1 | tail -6",
        t=200,
    )


def step_prototypes() -> None:
    run(
        "build prototype catalog",
        f"docker exec mindex-api python3 scripts/build_sine_prototype_catalog.py "
        f"--package-root {PKG} --audio-root {AUDIO} --metadata-csv {CSV} "
        f"--expected-model-id sine-esc50-cnn-p0-v1 --min-examples-per-label 5 "
        f"--write-json {PKG}/prototypes.json --write-sql {PKG}/register_prototypes.sql 2>&1 | tail -20",
        t=3600,
    )
    run(
        "register prototypes SQL",
        f"test -f {PKG}/register_prototypes.sql && "
        f"docker exec -i mindex-postgres psql -U mindex -d mindex -v ON_ERROR_STOP=1 -f - "
        f"< {PKG}/register_prototypes.sql 2>&1 | tail -8",
        t=300,
    )
    run("restart api", f"cd {REMOTE} && docker compose restart api && sleep 12")


def step_e2e() -> None:
    run(
        "E2E real-ai verifier",
        f"docker exec mindex-api python3 scripts/verify_sine_real_ai_e2e.py "
        f"--api-base http://127.0.0.1:8000 --query esc "
        f"--write-report {PKG}/e2e_real_ai_report.json 2>&1 | tail -30",
        t=900,
    )
    run(
        "final endpoint truth",
        f'cd {REMOTE} && set -a && . ./.env && set +a; TOK="${{MINDEX_INTERNAL_TOKENS%%,*}}"; '
        f'echo MODELS:; curl -s -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/sine/models | head -c 400; echo; '
        f'echo PROTOS:; curl -s -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/sine/prototypes | head -c 300',
    )


STEPS = {
    "build": step_build, "csv": step_csv, "train": step_train,
    "register": step_register, "prototypes": step_prototypes, "e2e": step_e2e,
}

if __name__ == "__main__":
    order = ["build", "csv", "train", "register", "prototypes", "e2e"]
    requested = sys.argv[1:] or order
    for name in requested:
        STEPS[name]()
    ssh.close()
    print("\nDone.")
