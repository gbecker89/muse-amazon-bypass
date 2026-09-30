# Muse Amazon Shopping Bypass

This fork adds read-only US order history and return-window reminders using
Amazon's stated per-item deadlines. See [Return-window reminders](#return-window-reminders).

This is a direct response to Amazon's block of AI agent shopping traffic on 9/20/2026.

Amazon product search and add-to-cart API for [Muse](https://muse.ai). Run it on the machine Muse can already SSH into, then point Muse at `http://127.0.0.1:8792`.

One connection covers two carts, `personal` and `business`. Each cart is a [Netscape cookie jar](https://curl.se/docs/http-cookies.html) exported from a normal browser. This package does not include anyone's cookies, hostname, or API key.

Search does not require a login. Add to cart does. Adding an item does not check out or pay.

## Set up

You need Python 3.11+ and `curl`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/aasper03/muse-amazon-bypass.git
mkdir -p ~/.amazon-cart
chmod 700 ~/.amazon-cart
openssl rand -hex 32 > ~/.amazon-cart/api-key
chmod 600 ~/.amazon-cart/api-key
```

Sign in to Amazon in a normal browser. Amazon blocks automated login windows. Export cookies for each account (`chmod 600`):

| Account | File |
|---------|------|
| `personal` | `~/.amazon-cart/cookies-personal.txt` |
| `business` | `~/.amazon-cart/cookies-business.txt` |

A business session includes a `b2b` cookie. A personal add is refused when that cookie is present, and a business add is refused when it is missing.

```bash
export AMAZON_API_KEY="$(cat ~/.amazon-cart/api-key)"
export HOST=127.0.0.1
export PORT=8792
amazon-cart
```

`MUSE_API_KEY` is accepted as an alias of `AMAZON_API_KEY`.

Confirm the server is up:

```bash
curl -sS http://127.0.0.1:8792/health
```

Expect `{"ok":true,"service":"amazon-cart"}`.

## Use from Muse over SSH

When Muse has a shell on the machine running `amazon-cart`, it calls the API on localhost. Leave the process bound to `127.0.0.1`. A public hostname and TLS are not part of this setup.

Tell Muse the SSH host, the base URL `http://127.0.0.1:8792`, and to send `Authorization: Bearer` with `AMAZON_API_KEY`. Keep the key out of chat. A prompt you can paste is in [MUSE.md](MUSE.md).

After SSH, Muse can run:

```bash
curl -sS -H "Authorization: Bearer $AMAZON_API_KEY" \
  'http://127.0.0.1:8792/search?query=usb-c+cable&max_results=5'

curl -sS -H "Authorization: Bearer $AMAZON_API_KEY" \
  'http://127.0.0.1:8792/cart/add?asin=B000000000&quantity=1&account=personal'
```

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness, no auth |
| GET | `/search?query=…` | Search products |
| GET | `/product/{asin}` | Product details |
| GET | `/product/{asin}/variations` | Variants |
| GET | `/cart?account=personal\|business` | Read that cart |
| GET | `/cart/add?asin=…&quantity=1&account=…` | Add to that cart |

`POST /cart` accepts `{"asin","quantity","region","account"}`. A missing login returns HTTP 409. An Amazon block page returns HTTP 503.

Use `account=personal` or `account=business` on cart routes. One key covers both carts.

### Local order-history support

`GET /orders?account=personal` reads US Amazon order history using the saved
session. With no `year`, it returns the past three months. Pass `year=2025`
(or another year) for older orders. Pages start at 1; follow `next_page`
until it is null. Each response includes order IDs, dates, totals, shipment
statuses, product titles and ASINs, plus `available_filters`. `order_count`
counts only the current page. This route reads orders and does not modify them.

If Amazon returns HTTP 409, open Your Orders in your browser, refresh the
cookie export, and set `AMAZON_USER_AGENT` to that browser's full user agent.
The local installation's `~/bin/amazon-cart-start` reads Google Chrome's
version automatically; `~/bin/amazon-cart-export-chrome` refreshes its cookies.

### Return-window reminders

`GET /returns?account=personal&reminder_days=7,2&timezone=America/Los_Angeles`
reads every order-history page in the past three months (up to `max_pages`,
default 10, maximum 20). Add `year=2026` to scan a specific year, including
older purchases with extended return windows. `coverage.complete` says whether
every page in the requested period was scanned; it does not cover other years.

Each order item includes Amazon's stated `return_window.deadline`, its
`source_text`, and status. Missing or unreadable deadlines are `unknown`, never
estimated from the order date. `/returns` reports `unknown_items` separately.
Expired windows and items visibly returned or with a return in progress do
not produce reminders. This feature currently supports the US English site.

The response includes `days_remaining` and `due_reminders` for eligible items
whose deadline is approaching. Each milestone has a stable `reminder_id` for
the caller to remember after sending a notification. Missed milestones remain
due with `catch_up=true` until the deadline passes. Dates use the requested
timezone (`AMAZON_TIMEZONE`, default UTC); there is no assumed deadline hour.

Muse must schedule the daily check and remember sent reminder IDs. The API
does not send notifications itself. A suggested schedule is 9 AM in your
timezone, with notifications at 7 days and 2 days before the deadline.
If both milestones are overdue on first setup, combine them into one reminder.
On an API failure, missing deadlines, or incomplete coverage, report the issue
instead of saying every return window was checked. Follow the response's
`order_url` to verify an item on Amazon.

Development checks: `python -m unittest discover -s tests -v`.

For the Muse prompts in [MUSE.md](MUSE.md), install the optional localhost
helper from this checkout. It reads `~/.amazon-cart/api-key` and supplies
the Bearer header without printing the key:

```bash
install -Dm700 scripts/amazon-cart-api "$HOME/bin/amazon-cart-api"
~/bin/amazon-cart-api '/returns?reminder_days=7,2&timezone=America/Los_Angeles'
```

## Optional: public HTTPS provider

Muse can also register this API with `credentials.request_api_access`. That connector stores `api_hosts` as a bare public hostname and calls it over HTTPS. Put TLS in front of port 8792 first. See [Caddyfile.example](Caddyfile.example) and the registration steps in [MUSE.md](MUSE.md).

For that connector, the hostname has no `https://` and no path. `placement` is `bearer_header` and cannot be changed later.

## Use without Muse

The same server is a normal HTTP API. Skip Muse and call it yourself with the Bearer key:

```bash
curl -sS -H "Authorization: Bearer $AMAZON_API_KEY" \
  'http://127.0.0.1:8792/search?query=usb-c+cable&max_results=5'
```

`X-Api-Key` is also accepted. Search accepts `max_results` (default 16) and `region` (default `us`): `us`, `uk`, `ca`, `de`, `fr`, `es`, `it`, `nl`, `jp`, `au`, `mx`, `in`, `ae`, `sa`, `ie`, `be`. `/regions` and `/products` are also available. `/products` is an alias for search.
