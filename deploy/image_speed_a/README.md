# api.acica.top Image Speed Plan A

This deployment uses two immutable layers:

- `chatgpt2api:api-acica-top-runtime-20260714`: the exact Python source from the running container before rollout, layered on the existing dependency and frontend image.
- `chatgpt2api:api-acica-top-image-speed-a-r3-20260714`: the runtime baseline plus the nine reviewed image-path source files.

The stable Compose tag remains `chatgpt2api:api-acica-top`. Rollout retags the candidate to the stable tag and recreates only `api-acica-top-app`.

Rollback:

```sh
docker tag chatgpt2api:api-acica-top-image-speed-a-r2-rollback-20260714 chatgpt2api:api-acica-top
docker compose up -d --no-deps --force-recreate app
```

Full baseline rollback remains available as `chatgpt2api:api-acica-top-runtime-20260714`.

Production acceptance (`2026-07-14`): `gpt-image-2`, `1024x1024`, `quality=auto`
completed in 27.289 seconds including task submission and polling. The returned image was
downloaded unchanged as a 1,236,979-byte PNG.

Runtime configuration:

```json
{
  "image_request_deadline_secs": 90,
  "image_sse_idle_timeout_secs": 20,
  "image_stream_close_timeout_secs": 0.5,
  "image_poll_initial_wait_secs": 2.5,
  "image_poll_interval_secs": 2,
  "image_poll_fast_window_secs": 30,
  "image_poll_slow_interval_secs": 5,
  "image_tasks_check_every": 4,
  "image_tasks_timeout_secs": 2,
  "image_heartbeat_interval_secs": 10,
  "image_account_probe_enabled": true,
  "image_account_probe_interval_secs": 300,
  "image_account_probe_batch_size": 3
}
```
