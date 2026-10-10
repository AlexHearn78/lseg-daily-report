from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_RPC_TOTAL_TIMEOUT = 90.0   # seconds — hard deadline per MCP RPC call
_TOKEN_REFRESH_MARGIN = 120.0  # refresh this many seconds before expiry


# ---------------------------------------------------------------------------
# Typed errors — callers can now distinguish auth / client / transient failures
# ---------------------------------------------------------------------------

class MCPError(RuntimeError):
    """Base class for MCP transport errors."""


class MCPAuthError(MCPError):
    """401/403 — token invalid or expired even after refresh. Abort the run."""


class MCPBadRequest(MCPError):
    """400 — request is malformed for this ticker/args. Permanent; do not retry."""


class MCPTimeout(MCPError):
    """RPC exceeded the hard deadline. Transient; retry with backoff."""


class MCPServerError(MCPError):
    """5xx / 429 / malformed SSE. Transient; retry with backoff."""


class MCPClient:
    """HTTP MCP client for LSEG's hosted MCP endpoint.

    Authenticates via OAuth2 client-credentials, then calls MCP tools
    over HTTP SSE transport. Tokens are refreshed before expiry and
    once more on a 401; a second consecutive 401 raises MCPAuthError.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://login.ciam.refinitiv.com/as/token.oauth2",
        endpoint: str = "https://api.analytics.lseg.com/lfa/mcp",
        timeout: float = 60.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.endpoint = endpoint
        self._http = httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=10.0),
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0  # time.monotonic() deadline

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _fetch_token(self) -> None:
        resp = self._http.post(
            self.token_url,
            data={"grant_type": "client_credentials", "scope": "lfa"},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        expires_in = float(body.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + expires_in
        logger.info("OAuth token acquired (expires in %.0fs)", expires_in)

    def _ensure_token(self) -> str:
        if (
            self._token is None
            or time.monotonic() >= self._token_expires_at - _TOKEN_REFRESH_MARGIN
        ):
            self._fetch_token()
        assert self._token is not None
        return self._token

    def invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    # ------------------------------------------------------------------
    # MCP RPC
    # ------------------------------------------------------------------

    def _initialize(self) -> dict:
        return self._rpc("initialize", protocolVersion="2024-11-05",
                         clientInfo={"name": "mdu-bot", "version": "1"},
                         capabilities={})

    def _do_request(self, payload: dict) -> dict:
        """One HTTP attempt. Raises typed MCPError subclasses.

        The deadline is enforced *inside* the SSE read loop: iter_raw yields
        at most every `read` timeout seconds, so worst-case overrun is one
        read-timeout beyond _RPC_TOTAL_TIMEOUT. No threads involved, so
        nothing can block past the deadline the way shutdown(wait=True) did.
        """
        deadline = time.monotonic() + _RPC_TOTAL_TIMEOUT
        try:
            with self._http.stream("POST", self.endpoint,
                                   headers=self._headers(), json=payload) as resp:
                if resp.status_code in (401, 403):
                    raise MCPAuthError(f"HTTP {resp.status_code} from MCP endpoint")
                if resp.status_code == 400:
                    raise MCPBadRequest("HTTP 400 from MCP endpoint")
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise MCPServerError(f"HTTP {resp.status_code} from MCP endpoint")
                resp.raise_for_status()

                chunks: list[bytes] = []
                for chunk in resp.iter_raw():
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise MCPTimeout(
                            f"SSE stream exceeded {_RPC_TOTAL_TIMEOUT:.0f}s deadline"
                        )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise MCPTimeout(f"Transport error/timeout: {e}") from e

        raw = b"".join(chunks).decode(errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise MCPServerError(f"No SSE data in response: {raw[:300]}")

    def _rpc(self, method: str, **params: object) -> dict:
        payload = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
        try:
            return self._do_request(payload)
        except MCPAuthError:
            # Token may have just expired — refresh once and replay.
            logger.warning("MCP RPC %s got auth error — refreshing token and retrying once", method)
            self.invalidate_token()
            return self._do_request(payload)  # second MCPAuthError propagates

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self._rpc("tools/call", name=name, arguments=arguments or {})

    def get_ibes_consensus(self, ticker: str, measures: list[str] | None = None,
                            period_type: str = "Year",
                            ticker_type: str = "RIC") -> list[dict]:
        """Fetch analyst consensus estimates (EPS, Rev, etc.).

        ``ticker`` is a RIC by default. A bare ticker such as ``NG`` matches
        several companies or none (it returns 404), so always pass a RIC.

        Returns list of estimate dicts with keys: date, periodIndex, measures.
        Returns empty list on data failure. Auth errors propagate.
        """
        try:
            resp = self.call_tool("qa_ibes_consensus", {
                "requests": [{
                    "dataType": "qa_ibes_consensus",
                    "options": {
                        "ticker": ticker,
                        "tickerType": ticker_type,
                        "measures": measures or ["Eps", "Rev"],
                        "periodType": period_type,
                    },
                }],
            })
            if resp.get("result", {}).get("isError"):
                return []
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data[0].get("response", {}).get("data", {}).get("estimates", [])
            return []
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch IBES consensus for %s: %s", ticker, e)
            return []

    def get_company_fundamentals(self, identifier: str, year: int = 2024,
                                  measures: str = "1001,5201,5145",
                                  freq: str = "A",
                                  id_type: str = "RIC") -> list[dict]:
        """Fetch reported company fundamentals.

        Returns list of fundamental records. Empty list on data failure.
        Auth errors propagate.
        """
        try:
            resp = self.call_tool("qa_company_fundamentals", {
                "requests": [{
                    "dataType": "qa_company_fundamentals",
                    "options": {
                        "identifier": identifier,
                        "type": id_type,
                        "measures": measures,
                        "year": year,
                        "freq": freq,
                    },
                }],
            })
            if resp.get("result", {}).get("isError"):
                return []
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data[0].get("response", {}).get("data", {}).get("fundamentals", [])
            return []
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch fundamentals for %s: %s", identifier, e)
            return []

    def __enter__(self) -> MCPClient:
        self._http.__enter__()
        self._initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self._http.__exit__(*args)

    # ------------------------------------------------------------------
    # High-level fetchers
    # ------------------------------------------------------------------

    def get_historical_prices(self, ric: str, interval: str = "P1D", count: int = 500,
                              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Fetch historical price summaries.

        With ``start`` (YYYY-MM-DD, optionally ``end``) the date range is sent
        instead of ``count``. The service has been returning only about a
        month of rows for ``count`` requests, while a date range returns the
        whole window.

        Returns DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        args: dict[str, Any] = {"universe": ric, "interval": interval}
        if start:
            args["start"] = start
            if end:
                args["end"] = end
        else:
            args["count"] = count
        resp = self.call_tool("historical_pricing_summaries", args)
        raw = self._extract_text(resp)
        if not raw or raw.strip() == "":
            logger.warning("Empty response for historical prices: %s", ric)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON for historical prices %s: %s", ric, e)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        records = []
        for item in data:
            headers = [h["name"] for h in item.get("headers", [])]
            rows = item.get("data", item.get("summaries", []))
            for row in rows:
                record = dict(zip(headers, row))
                close = record.get("TRDPRC_1")
                if close is None:
                    close = record.get("OFF_CLOSE")
                if close is None:
                    bid, ask = record.get("BID"), record.get("ASK")
                    if bid is not None and ask is not None:
                        close = (bid + ask) / 2
                records.append({
                    "timestamp": record.get("DATE"),
                    "open": record.get("OPEN_PRC"),
                    "high": record.get("HIGH_1"),
                    "low": record.get("LOW_1"),
                    "close": close,
                    "volume": record.get("ACVOL_UNS"),
                })
        df = pd.DataFrame(records)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def get_equity_vol_surface(self, ric: str, calculation_date: str | None = None) -> pd.DataFrame:
        """Fetch an equity vol surface; empty DataFrame on data failure.

        Columns are moneyness in percent (100 = at the money) and values are
        implied vols as decimals (0.42 = 42%), the units research._module_vol
        reads. The tool itself returns moneyness as a fraction and vols in
        percent.
        """
        try:
            instrument = ric if ric.endswith("@RIC") else f"{ric}@RIC"
            surfaces: dict[str, Any] = {"date_format": "Date", "strike_format": "Moneyness"}
            if calculation_date:
                surfaces["dates"] = [calculation_date]
            args = {"instrument": instrument, "surfaces": surfaces, "smiles": None}
            resp = self.call_tool("equity_vol_surface", args)
            if resp.get("result", {}).get("isError"):
                logger.warning("equity_vol_surface unavailable for %s", ric)
                return pd.DataFrame()
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, list) and data:
                item = data[0]
                surface = item["surface"]
                headers = surface[0][1:]
                data_rows = surface[1:]
                expiry_dates = [r[0] for r in data_rows]
                vals = [r[1:] for r in data_rows]
                df = pd.DataFrame(vals, index=expiry_dates,
                                  columns=[round(float(h) * 100, 4) for h in headers]) / 100
                df.index.name = "expiry"
                df.columns.name = "strike"
                return df
            return pd.DataFrame()
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch vol surface for %s: %s", ric, e)
            return pd.DataFrame()

    def get_index_return_series(self, index_id: str, from_date: str, to_date: str | None = None,
                                 frequency: str = "D") -> pd.DataFrame:
        """Fetch index return time series. Returns empty DataFrame on data failure."""
        try:
            resp = self.call_tool("ixm_index_return_time_series", {
                "baseIndexId": index_id, "fromDate": from_date, "frequency": frequency,
                **(to_date and {"toDate": to_date} or {}),
            })
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, dict) and "points" in data:
                pts = data["points"]
                return pd.DataFrame(pts.get("rows", []), columns=pts.get("columns", []))
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch index return series: %s", e)
        return pd.DataFrame()

    def get_index_risk_series(self, index_id: str, from_date: str, metric: str = "IX_EFFDUR",
                               to_date: str | None = None, frequency: str = "D") -> pd.DataFrame:
        """Fetch index risk time series. Returns empty DataFrame on data failure."""
        try:
            resp = self.call_tool("ixm_index_risk_time_series", {
                "baseIndexId": index_id, "fromDate": from_date,
                "metricKeyword": metric, "frequency": frequency,
                **(to_date and {"toDate": to_date} or {}),
            })
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, dict) and "points" in data:
                pts = data["points"]
                return pd.DataFrame(pts.get("rows", []), columns=pts.get("columns", []))
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch index risk series: %s", e)
        return pd.DataFrame()

    def get_interest_rate_curve(self, reference: str = "LSEG/USD_SOFR_Swap_ZC_Curve",
                                 valuation_date: str | None = None) -> pd.DataFrame:
        """Fetch interest rate curve. Returns DataFrame with columns: tenor, rate, endDate, etc."""
        try:
            args = {"reference": reference}
            if valuation_date:
                args["pricingPreferences"] = {"valuationDate": valuation_date}
            resp = self.call_tool("interest_rate_curve", args)
            if resp.get("result", {}).get("isError"):
                logger.warning("interest_rate_curve unavailable for %s", reference)
                return pd.DataFrame()
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, dict) and "zcPoints" in data:
                zc = data["zcPoints"]
                cols = zc.get("columns", [])
                rows = zc.get("rows", [])
                df = pd.DataFrame(rows, columns=cols)
                if "rate" in df.columns:
                    df["rate"] = df["rate"].astype(float)
                return df
            if isinstance(data, list):
                return pd.DataFrame(data)
            return pd.DataFrame()
        except MCPAuthError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch interest_rate_curve: %s", e)
            return pd.DataFrame()

    def get_company_news(self, ric: str, start: str | None = None, end: str | None = None,
                         headline_only: bool = False) -> list[dict]:
        """Fetch important company news.

        If the RIC resolves to multiple entities (disambiguation), a second
        call is made with the first candidate RIC.
        """
        try:
            args = {"companyRics": ric, "headlineOnly": headline_only}
            if start:
                args["start"] = start
            if end:
                args["end"] = end
            resp = self.call_tool("important_company_news", args)
            if resp.get("result", {}).get("isError"):
                return []
            raw = self._extract_text(resp)
            data = json.loads(raw)
            if isinstance(data, dict) and "headlines" in data:
                return data["headlines"]
            if isinstance(data, list):
                return data
            return []
        except MCPAuthError:
            raise
        except (json.JSONDecodeError, TypeError):
            pass
        # Multi-turn disambiguation — try first candidate RIC
        try:
            raw = self._extract_text(resp)
            for line in raw.splitlines():
                if "RIC:" in line:
                    cand_ric = line.split("RIC:")[-1].split(",")[0].strip().strip(")")
                    if cand_ric:
                        args["companyRics"] = cand_ric
                        resp2 = self.call_tool("important_company_news", args)
                        raw2 = self._extract_text(resp2)
                        data2 = json.loads(raw2)
                        if isinstance(data2, dict):
                            return data2.get("headlines", [])
                        return data2 if isinstance(data2, list) else []
        except MCPAuthError:
            raise
        except Exception as e2:
            logger.warning("News disambiguation failed for %s: %s", ric, e2)
        return []

    @staticmethod
    def _extract_text(resp: dict) -> str:
        content = resp.get("result", {}).get("content", [])
        if isinstance(content, list) and content:
            item = content[0]
            if isinstance(item, dict):
                return item.get("text", json.dumps(item))
        return json.dumps(content)
