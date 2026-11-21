# Nusuk v2 API Contract (source of truth for frontend)

All device identity = `device_uuid` (string UUID). Statuses: OFFLINE, IDLE, RUNNING, RECOVERING, STOPPING, REBOOTING, ERROR.
Modes: answer_only | redeem_only | auto. Timestamps are naive UTC ISO strings (no 'Z') — frontend treats them as UTC as in v1.

## InstanceDTO
```
{ uuid, name, ip, port, address:"ip:port", status, mode|null, current_screen, current_step,
  current_app_keys:int|null, uptime_seconds:int, live_preview_enabled:bool, nusuk_installed:bool|null,
  anchor_planted:bool, is_legacy_primary:bool, last_seen_at|null,
  eligible_keys:int, used_keys:int, invalid_keys:int }
```

## Instances  (prefix /api/instances)
- `GET  /api/instances` -> `{items:[InstanceDTO]}` (active only)
- `GET  /api/instances/archived` -> `{items:[{uuid,name,ip,port,deleted_at}]}`
- `POST /api/instances/test-connection` body `{ip,port}` ->
  `{adb_ok, nusuk_installed:bool|null, message, already_registered:bool, known_device:{uuid,name,is_deleted,address}|null, clone_conflict:bool, can_save:bool}`
  (`clone_conflict`=true: same UUID file is live at another IP:Port; saving requires `force_new_uuid:true`.)
- `POST /api/instances` body `{name?,ip,port,force_new_uuid?:false}` -> `{success,message,data:{instance:InstanceDTO,restored:bool}}` (400/409 with `detail` on rejection)
- `PATCH /api/instances/{uuid}` body `{name?,ip?,port?,live_preview_enabled?}` -> `{success,message,data:InstanceDTO}`
- `DELETE /api/instances/{uuid}` -> soft-delete. `{success,message}`
- `POST /api/instances/{uuid}/start` `{mode}` ; `/stop` ; `/force-restart-app` ; `/force-restart-device` ; `/reconnect` -> `{success,message}`
- `GET  /api/instances/{uuid}/screenshot` -> image/png. If preview OFF: HTTP **409** `{detail:"preview_disabled"}` (backend never touches ADB). Offline/rebooting: HTTP 503 `{detail}`.

## Bulk / control (prefix /api/control)
- `GET  /api/control/status` -> `{database_connected, has_ai_key, total, online, offline, running, idle, recovering, rebooting, error, stopping}`
- `POST /api/control/bulk/start` `{device_uuids:[], mode}` -> `{success, message, started:[{uuid,name}], switched:[{uuid,name,from_mode,to_mode}], already_running:[{uuid,name,mode}], skipped:[{uuid,name,reason}]}` (partial success + direct mode switching; HTTP 400 only if whole request invalid e.g. AI key missing for answer_only/auto)
- `POST /api/control/bulk/stop` `{device_uuids}` -> same shape (`started` = stopped list)
- `POST /api/control/bulk/force-restart` `{device_uuids, kind:"app"|"device"}` -> same shape
- `POST /api/control/reconnect-all` -> `{success,message}`

## Analytics (prefix /api/analytics)
- `GET /api/analytics/kpi` ->
```
{ global:{ total_instances, online_instances, total_app_keys, unused_pure, blacklisted, total_master_keys,
           used_claims, total_quiz_answered, today_quiz_answered, accuracy_percentage, correct, incorrect,
           avg_duration_sec, avg_ai_duration_sec|null, avg_redeem_duration_sec, auto_recovery_count,
           running_count, idle_count, offline_count },
  instances:[{ uuid,name,address,status,current_app_keys,eligible_keys,used_keys,invalid_keys,
               quiz_total,avg_duration_sec,avg_ai_duration_sec|null,avg_redeem_duration_sec,
               recovery_count, circuit_breaker_limit, consecutive_recoveries }] }
```
- `GET /api/analytics/charts?timeframe=24h|7d|30d|all&device_uuid=all|<uuid>` ->
  `{timeframe, device_uuid, key_distribution:[{status,count}], door_performance:[{door,total,correct,accuracy}],
    quiz_trend:[{time,total,correct,avg_duration_sec,avg_ai_duration_sec|null}], redeem_trend:[{time,used,invalid}],
    instance_comparison:[{uuid,name,quiz_total,keys_used,avg_duration_sec,avg_ai_duration_sec|null}]}`
  key_distribution statuses — global: `unused_pure`, `used_partial` (>=1 device used), `blacklisted`; per-device: `available`, `used`, `invalid`.

## Keys (prefix /api/keys)
- `GET /api/keys?page&page_size&status&search&device_uuid=` -> `{total,page,page_size,items:[KeyDTO],summary:{total,unused_pure,claimed,blacklisted}}`
  - Master view (`device_uuid` empty) status filter: `all|unused_pure|claimed|blacklisted`.
  - Device perspective status filter: `all|available|in_progress|used|invalid`.
  - KeyDTO: `{id,code,source,created_at,is_blacklisted,blacklisted_by_device_uuid,blacklisted_by_name,blacklist_reason,blacklisted_at,used_count,total_devices, device_status|null, device_duration_ms|null, device_error_note|null}`
- `GET /api/keys/{id}/claims` -> `{key:KeyDTO, claims:[{device_uuid,device_name,status,duration_ms,error_note,created_at,completed_at,is_blacklist_source}]}`
- `POST /api/keys/manual` `{code}` ; `POST /api/keys/bulk` `{raw_text}` -> as v1
- `POST /api/keys/{id}/reset?device_uuid=` : no device = global blacklist reset (clears blacklist + all `invalid` claims); with device = remove that device's used/invalid claim (not in_progress)
- `DELETE /api/keys/{id}` ; `DELETE /api/keys/actions/clear-invalid` (deletes blacklisted master keys)
- `GET /api/keys/actions/export?device_uuid=` -> text/plain; empty = pure-unused global, else unused for that device

## Logs (prefix /api/logs)
- `GET /api/logs/quiz?page&page_size&result&door&device_uuid=all|<uuid>` items add `device_uuid, device_name, device_archived:bool, ai_duration_ms|null`
- `GET /api/logs/system?page&page_size&severity&device_uuid=all|GLOBAL|<uuid>` items add `device_uuid|null, device_name|null`
- `GET /api/logs/devices` -> `{items:[{uuid,name,is_deleted}]}` (all incl. archived, for filter dropdowns)

## Settings (prefix /api/settings)
- `GET` adds `watchdog_interval_seconds`, `preview_refresh_seconds`; `adb_target` is no longer returned/accepted.
- `POST` body: ai_base_url, ai_api_key, ai_model, loading_timeout_seconds, circuit_breaker_limit, action_delay_seconds, watchdog_interval_seconds, preview_refresh_seconds
- `POST /api/settings/test-ai` unchanged.

## WebSocket `/ws/telemetry` — every message `{type, data}`
- `state_sync` `{instances:[InstanceDTO]}` (on connect)
- `device_list_update` `{instances:[InstanceDTO]}` (add/restore/rename/soft-delete)
- `status_update` `{device_uuid,status,mode,current_screen,current_step,uptime_seconds,current_app_keys}`
- `app_keys_update` `{device_uuid,current_app_keys}`
- `preview_setting_update` `{device_uuid,live_preview_enabled}`
- `log_event` `{id,device_uuid|null,device_name|null,severity,category,message,last_screen_state,created_at}` (`device_uuid` null = GLOBAL)
- `quiz_event` `{device_uuid,id,door,question,chosen_answer,is_correct,duration_ms,ai_duration_ms}`
- `key_event` `{device_uuid,action,code,id,note,duration_ms}`
- `alert_event` `{device_uuid|null,type:"info"|"warning"|"error",message}`
