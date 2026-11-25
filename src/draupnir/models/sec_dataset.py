"""One quarter of SEC Financial Statement Data Sets, as loaded DataFrames."""

from typing import NamedTuple

import pandas as pd


class SecDataset(NamedTuple):
    sub: pd.DataFrame  # submissions: one row per filing (adsh), company info
    num: pd.DataFrame  # numbers: adsh, tag, ddate, qtrs, uom, value
    pre: pd.DataFrame  # presentation: adsh, report, line, stmt, tag, plabel
