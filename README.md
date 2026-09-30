# ThreatDown OneView API Tools

Tools for the ThreatDown OneView (MSP) API:

- **`oneview_bulk_delete.py`** is a desktop app for deleting many endpoints from a site by computer name.
- **`oneview_api.py`** is a reusable Python client for the OneView API.

## Requirements

- Python 3.10 or newer
- `requests`: `pip install requests`
- Tkinter (GUI only). It comes with Python on Windows and macOS. On Linux, install it with your package manager, e.g. `sudo apt install python3-tk`.

Keep both `.py` files in the same folder.

## API credentials

1. In OneView, go to **Integrate** and create an API client.
2. Grant it **read**, **write** and **execute** scopes. Deleting endpoints needs more than read.
3. Copy the client ID and secret.

## config.json (optional)

Credentials can be saved in `config.json` next to the scripts:

```json
{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "base_url": "https://api.threatdown.com",
  "timeout": 60
}
```

All fields are optional. `base_url` and `timeout` default to the values shown.

The GUI creates this file for you when **Remember credentials** is ticked. The secret is stored as plain text, so don't commit this file or put it on a shared drive.

## Bulk delete (GUI)

```
python3 oneview_bulk_delete.py
```

1. **Credentials.** Enter the client ID and secret, or let them load from `config.json`, then click **Connect**. If **Remember credentials** is ticked, a successful connect saves them to `config.json`. If it's unticked, any saved credentials are cleared.
2. **Select site.** Only sites with an active subscription are listed.
3. **Paste computer names.** Put them one per line, or separate them with commas, semicolons, tabs or spaces. Matching ignores case, and a short name like `PC-01` matches `PC-01.corp.local`.
4. **Look up.** The log shows the result for each name:
   - `OK`: exactly one endpoint matched. It will be deleted.
   - `??`: several endpoints share the name. These are **skipped**; delete them in OneView by hand.
   - `--`: no endpoint with that name in the selected site.
5. **Delete matched.** Leave **Dry run** ticked to see what would be deleted without deleting anything. Untick it to delete for real. Each run asks for confirmation.

Notes:

- Deleting an endpoint cannot be undone. OneView automatically flags the agent for uninstall.
- By default, isolated endpoints are not force-deleted. To include them, set `UNREGISTER_ISOLATED = True` at the top of `oneview_bulk_delete.py`.
- Deletes are spaced 0.25 s apart to stay under the API limit of 360 requests per minute.
- Changing the site or the name list clears the matches, so run **Look up** again before deleting.

## Using the API client

```python
from oneview_api import ApiError, OneViewClient, endpoint_id, match_names, parse_names

api = OneViewClient.from_config("config.json")
# or: api = OneViewClient(); api.authenticate(client_id, client_secret)

for site in api.get_sites():
    endpoints = api.get_endpoints(site.account_id)
    print(site.name, len(endpoints))

site = api.get_sites()[0]
result = match_names(parse_names("PC-01, PC-02"), api.get_endpoints(site.account_id))
for name, endpoint in result.matched:
    try:
        api.delete_endpoint(site.account_id, endpoint_id(endpoint))
    except ApiError as exc:
        print(f"{name}: {exc}")
```

### Reference

| Name | Description |
| --- | --- |
| `OneViewClient(base_url=..., timeout=60)` | API client. Retries automatically when rate-limited (HTTP 429). |
| `OneViewClient.from_config(config)` | Build and authenticate a client from a `Config` or a path to `config.json`. |
| `.authenticate(client_id, client_secret)` | Get an OAuth2 token. |
| `.get_sites()` | `list[Site]` with `name` and `account_id`, sorted by name. |
| `.get_endpoints(account_id)` | All endpoints in a site, as raw API dicts. Fetches every page. |
| `.delete_endpoint(account_id, endpoint_id, unregister_isolated=False)` | Delete one endpoint. |
| `Config.load(path)` / `config.save(path)` | Read or write `config.json`. |
| `parse_names(text)` | Split pasted text into unique names. |
| `match_names(names, endpoints)` | Returns `MatchResult(matched, ambiguous, not_found)`. |
| `endpoint_id(ep)` / `endpoint_label(ep)` / `endpoint_names(ep)` | Get the ID, display name, or all name variants of an endpoint. |

Failed API calls raise `ApiError`. The message includes the HTTP status and the API's response.

## Troubleshooting

- **Connect fails with HTTP 401.** The client ID or secret is wrong.
- **Connect fails with HTTP 400.** Check the error text in the log. If the API client lacks some scopes, the tool already retries without asking for specific scopes.
- **Every name shows as "Not found".** Make sure the right site is selected. The names must match the hostnames OneView shows for those endpoints.
- **A delete fails with HTTP 400/403.** The API client may lack the write/execute scope, or the endpoint may be isolated (see `UNREGISTER_ISOLATED`).
- **"Could not read config.json".** The file isn't valid JSON. Fix it or delete it.

API documentation: <https://oneview.threatdown.com/oauth/documentation/msp> (requires a OneView login)
