# SINE + MINDEX + NAS Stack — Complete (May 27, 2026)

**Date:** 2026-05-27  
**Status:** Complete (backend + BFF); VM disk prune + MQTT broker auth are ops follow-ups  
**Codex handoff:** `WEBSITE/website/docs/codex-handoffs/SINE_MINDEX_STACK_CODEX_FRONTEND_TEST_HANDOFF_MAY27_2026.md`

---

## Delivered

| Area | Outcome |
|------|---------|
| SINE acoustic API | `/api/mindex/sine/*` on VM **189**, 7 detectors, **2180** blobs |
| NAS library | CIFS `//192.168.0.105/mycosoft.com/mindex` → `/mnt/nas/mindex`, `remote_nas: true` |
| Internal auth | `MINDEX_INTERNAL_TOKENS` synced; BFF + direct API **200** |
| Website BFF | `/api/mindex/sine/*`, `/api/mindex/library/storage`, `/api/natureos/mindex/console` → **200** on **3010** |
| Player page | `/sensing/sine/player` → **200** |
| API container | `mindex-api` with `mindex_api` + `mindex_etl` + NAS bind mounts |
| MQTT bridge unit | `mqtt-mycobrain-bridge.service` deployed on **188** (broker auth fix pending) |

---

## Verify commands

```powershell
$h = @{ "X-Internal-Token" = $env:MINDEX_INTERNAL_TOKEN }
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/sine/status -Headers $h
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/library/storage -Headers $h
Invoke-RestMethod http://localhost:3010/api/mindex/sine/status
```

Scripts: `_recreate_api_with_etl_may27_2026.py`, `_verify_stack_may27_2026.py`, `deploy_mqtt_bridge_mas_vm.py` (MAS repo).

---

## Known follow-ups

1. **VM 189 disk:** rsync library to NAS, then remove `/var/lib/mindex-nas-local-backup-20260604005520` (~88 GB).
2. **MQTT:** bridge on **188** reports `Not authorized` to **196** — align `MYCOBRAIN_MQTT_PASSWORD` / broker ACL (`mqtt_broker_auth_smoke_ssh.py`). Not required for SINE player.
3. **SINE analyze:** install `mindex[sine]` extras in API container for full `POST .../analyze` pipeline.

---

## Related

- `docs/SINE_ACOUSTIC_BACKEND_MAY27_2026.md`
- `docs/MINDEX_LIBRARY_NAS_MOUNT_MAY27_2026.md`
- `docs/STACK_VERIFY_MAY27_2026.json`
