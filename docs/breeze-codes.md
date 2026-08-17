# Breeze stock codes

Breeze does **not** use NSE ticker symbols. It uses its own short codes, and
passing an NSE ticker returns empty data rather than an error — which looks
exactly like "no candles available" and is a common source of silent failure
when configuring `EQUITY_UNIVERSE`.

## Examples

| Company | NSE ticker | Breeze code |
|---|---|---|
| Reliance Industries | RELIANCE | `RELIND` |
| Tata Consultancy Services | TCS | `TCS` |
| Infosys | INFY | `INFTEC` |
| HDFC Bank | HDFCBANK | `HDFBAN` |
| ICICI Bank | ICICIBANK | `ICIBAN` |
| State Bank of India | SBIN | `STABAN` |
| Larsen & Toubro | LT | `LARTOU` |
| Bharti Airtel | BHARTIARTL | `BHAAIR` |
| ITC | ITC | `ITC` |
| Axis Bank | AXISBANK | `AXIBAN` |
| Kotak Mahindra Bank | KOTAKBANK | `KOTMAH` |
| Hindustan Unilever | HINDUNILVR | `HINLEV` |
| Nifty 50 index | NIFTY 50 | `NIFTY` |
| Bank Nifty index | NIFTY BANK | `CNXBAN` |

Treat this table as a starting point, not an authority — verify each code
against the official list before trading it.

## Finding a code

The authoritative list is the security master file published by ICICI
Securities, linked from the Breeze API documentation at
<https://api.icicidirect.com/breezeapi/documents/index.html>. Download it and
search by company name.

To check a single code interactively once you have a session:

```python
from app.data.breeze_client import BreezeClient

client = BreezeClient()
client.connect("<today's session token>")

print(client.get_quote("RELIND"))   # populated dict => the code is valid
print(client.get_quote("RELIANCE")) # empty dict     => wrong code
```

## Verifying your universe

After configuring `EQUITY_UNIVERSE`, connect through the dashboard and check the
backfill result. Any symbol reporting `0` candles is almost always a wrong code
rather than a data outage:

```bash
curl -s localhost:8000/api/status | python -m json.tool
```

## Exchange codes

| Segment | `exchange_code` |
|---|---|
| NSE cash | `NSE` |
| BSE cash | `BSE` |
| NSE F&O | `NFO` |
| Currency | `NDX` |
| Commodity | `MCX` |

## Derivatives

Options and futures need extra parameters alongside the stock code:

```python
client.get_historical_data(
    stock_code="NIFTY",
    exchange_code="NFO",
    product_type="options",
    expiry_date="2025-01-30T06:00:00.000Z",
    right="call",           # "call" | "put" | "others"
    strike_price="23000",
    from_date=start,
    to_date=end,
    interval="5minute",
)
```

Expiry dates must be the exact contract expiry in Breeze's ISO format, and
option quantities must be whole multiples of the contract lot size — the risk
manager handles that rounding via its `lot_size` parameter.
