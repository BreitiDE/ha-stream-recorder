# Stream Recorder

The complete user manual is built into the app: open **Stream Recorder** in the sidebar and
click **Manual** (German and English). This page covers installation and configuration.

*Deutsch: Die vollständige Bedienungsanleitung steht in der App unter **Anleitung**.*

## Start

1. Adjust the configuration below if needed and start the app.
2. Turn on **Show in sidebar** and open **Stream Recorder**.
3. Add a station under **Stations** (the sunshine live Lounge stream is already there),
   test it, then create a job with **New job** or start right away with **Record now**.

## Configuration

| Option | Meaning |
|---|---|
| **Storage folder** (`storage_path`) | Default `/media/stream-recorder`. Must be below `/media` or `/share`. The app creates `record/` (recordings), `title/` (single tracks) and `deleted/` (trash) in it. |
| **Username / Password** | Only for direct access in the browser without Home Assistant. Both empty = direct access off. |
| **Log level** | Detail of the app log (log page in the app). Nothing is written to the Home Assistant log. |
| **Network: 8765/tcp** | Port for direct access, e.g. `8765`. Empty = sidebar only. |

Restart the app after changing the configuration.

**Recording to a NAS:** add a network share under *Settings › System › Storage* with usage
**Media**, e.g. named `nas`, and set the storage folder to `/media/nas/stream-recorder`.

**Direct access:** set username, password and a port, then open
`http://<home-assistant-ip>:<port>/`. Only use it in your home network (no HTTPS); from
outside, use the sidebar.

## Home Assistant integration

Entities (updated on every change, recreated within 10 minutes after a Home Assistant restart):

- `binary_sensor.stream_recorder_recording` – `on` while recording
- `sensor.stream_recorder_next_recording` – start of the next recording (timestamp)
- `sensor.stream_recorder_last_recording` – `ok`, `aborted` or `error`, with title, message and file
- `sensor.stream_recorder_planned` – number of planned jobs
- `sensor.stream_recorder_disk_free` – free storage in GB

Events: `stream_recorder_finished` after every recording, `stream_recorder_error` on errors.
Data: `title`, `sender`, `status`, `message`, `file`, `duration_s`, `started`, `ended`.

Commands with the action `hassio.app_stdin` (the app name is shown in the address bar of the
app page, e.g. `f91e2bd5_stream_recorder`):

```yaml
action: hassio.app_stdin
data:
  app: f91e2bd5_stream_recorder
  input:
    action: record          # or: stop
    sender: sunshine live Lounge
    duration: 60            # minutes
    title: Recording by automation
```

## Data and backup

- Database, settings and log are in the app's `/data` folder and part of the Home
  Assistant backup.
- Recordings are in the storage folder. They are only in the backup if you include the
  media folder.

## Support

Issues and source: <https://github.com/BreitiDE/ha-stream-recorder>

☕ Like the app? [Donate via PayPal](https://paypal.me/breitide) – thank you!
