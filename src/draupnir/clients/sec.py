"""SEC EDGAR client.

Downloads quarterly Financial Statement Data Sets, loads them as DataFrames, and scrapes
shares outstanding from a filing's R page when the data set lacks it. Requests are throttled
to SEC's fair-access limit and always carry the configured User-Agent.
"""

import csv
import io
import logging
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from lxml import html

from draupnir.models.sec_dataset import SecDataset

log = logging.getLogger(__name__)

DATASET_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
DATASET_FILES = ("sub.txt", "num.txt", "pre.txt")
MIN_INTERVAL_S = 0.11  # SEC allows at most 10 requests/second
ATTEMPTS = 4


class SecClient:
    def __init__(self, user_agent: str, raw_dir: Path):
        self.raw_dir = raw_dir
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    def _get(self, url: str) -> requests.Response:
        """GET with throttling; retries connection errors and 5xx/429 up to ATTEMPTS times."""
        for attempt in range(1, ATTEMPTS + 1):
            wait = self._last_request + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self._session.get(url, timeout=60)
            except requests.RequestException as exc:
                if attempt == ATTEMPTS:
                    raise
                log.warning("GET %s failed (%s); retry %d", url, exc, attempt)
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                if attempt == ATTEMPTS:
                    return response
                log.warning("GET %s -> %d; retry %d", url, response.status_code, attempt)
            time.sleep(0.5 * attempt)
        raise AssertionError("unreachable")

    def dataset_dir(self, quarter: str) -> Path:
        return self.raw_dir / quarter.lower()

    def download_dataset(self, quarter: str) -> Path:
        """Fetch and extract sub/num/pre for `quarter` (e.g. '2009Q4') unless already present."""
        target = self.dataset_dir(quarter)
        if all((target / name).exists() for name in DATASET_FILES):
            return target
        url = DATASET_URL.format(quarter=quarter.lower())
        log.info("downloading %s", url)
        response = self._get(url)
        response.raise_for_status()
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for name in DATASET_FILES:
                (target / name).write_bytes(archive.read(name))
        return target

    def load_dataset(self, quarter: str) -> SecDataset:
        directory = self.download_dataset(quarter)
        sub = _read_tsv(directory / "sub.txt")
        num = _read_tsv(directory / "num.txt", usecols=["adsh", "tag", "ddate", "qtrs", "uom", "value"])
        pre = _read_tsv(directory / "pre.txt", usecols=["adsh", "report", "line", "stmt", "tag", "plabel"])
        num["value"] = pd.to_numeric(num["value"], errors="coerce")
        for column in ("ddate", "qtrs"):
            num[column] = pd.to_numeric(num[column], errors="coerce").astype("Int64")
        for column in ("report", "line"):
            pre[column] = pd.to_numeric(pre[column], errors="coerce").astype("Int64")
        return SecDataset(sub=sub, num=num, pre=pre)

    def shares_outstanding(self, filing_url: str, report: str) -> float | None:
        """Sum of 'shares outstanding' rows on the filing's R{report}.htm page, or None.

        Used when a company reports several share classes and the data set has no single value.
        """
        response = self._get(f"{filing_url}/R{report}.htm")
        if response.status_code != 200:
            return None
        page = html.fromstring(response.content)
        title = " ".join(page.xpath("//title//text()"))
        if "Request Rate Threshold Exceeded" in title:
            log.warning("SEC rate threshold exceeded while fetching %s", filing_url)
            return None
        rows = [row.text_content() for row in page.xpath("//tr[@class='ro'] | //tr[@class='re']")]
        total = 0
        for row in rows:
            text = row.replace(",", "").upper()
            if "SHARES OUTSTANDING" in text:
                # KNOWN-BUG(SHARES-DIGITS): keeps every digit in the row, label digits included.
                digits = re.sub(r"\D+", "", text)
                total += int(digits) if digits else 0
        return float(total) if total else None


def _read_tsv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(
        path,
        sep="\t",
        quoting=csv.QUOTE_NONE,
        dtype=str,
        keep_default_na=False,
        usecols=usecols,
        encoding="utf-8",
        encoding_errors="replace",
    )
