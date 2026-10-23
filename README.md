# Good Morning Dashboard

A small FastAPI service that says when to leave for the train. It watches
SEPTA Regional Rail for a morning and evening commute (including trips that
need a transfer), adds the weather for the walk, and sends a push notification
on weekday afternoons with the recommended train home.

Built to be polled by Home Assistant for a wall-mounted dashboard.

## What it does

- **Trip planning with transfers.** Uses a direct trip when SEPTA has one.
  Otherwise it queries each leg separately and stitches them together, picking
  the earliest connecting train that can still be caught.
- **Live delay and stall tracking.** Pulls SEPTA TrainView positions to
  catch delays on either leg, and flags trains whose GPS position has stopped
  moving.
- **Leave-by times.** Accounts for the walk to the station, so the dashboard
  shows when to walk out the door, not just when the train departs.
- **Service alerts.** Filters SEPTA alerts down to the two lines that matter
  and separates urgent alerts from standing advisories.
- **Weather.** Open-Meteo forecast for both commute windows, with a clothing
  suggestion.
- **Fails soft.** Caches the last good SEPTA and weather responses and serves
  them when an upstream API times out.
- **Afternoon notification.** A scheduled job sends one ntfy.sh push each
  weekday with the recommended train home.

## API

| Endpoint | Purpose |
|---|---|
| `GET /` | Health check |
| `GET /api/dashboard` | Combined commute, weather and alerts payload |
| `GET /api/commute_morning` | Home to work |
| `GET /api/commute_return` | Work to home |
| `GET /api/commute` | Any origin/destination (query params) |
| `GET /api/alerts` | Filtered SEPTA alerts |
| `GET /api/weather` | Commute weather |
| `POST /api/test_notification` | Run the afternoon check now |

## Running it

```bash
cp .env.example .env          # set NTFY_TOPIC and your stations
docker build -t good-morning-dashboard .
docker run -d --env-file .env -p 127.0.0.1:8000:8000 good-morning-dashboard
```

The API has no authentication, so publish the port on `127.0.0.1` (as above)
or another trusted interface only. The image defaults to
`TZ=America/New_York`; pass `-e TZ=...` to change it.

Or without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Your stations, walk times, target train home, push time and forecast
location are environment variables; see [.env.example](.env.example). Delay
thresholds and commute windows are in [app/config.py](app/config.py).

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

All external API calls are mocked. CI runs the tests and a gitleaks secret
scan on every push.

## License

Apache 2.0. See [LICENSE](LICENSE).
