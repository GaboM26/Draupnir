"""Company: one row per filer, taken from SEC submissions (sub.txt) plus our ticker and load period."""

# Column order of the `company` table. `ein_id` is the key (KNOWN-BUG(EIN-KEY): should be CIK).
COMPANY_COLUMNS = (
    "ein_id", "adsh", "cik_id", "name", "sic", "countryba", "stprba", "cityba", "zipba",
    "bas1", "bas2", "baph", "countryma", "stprma", "cityma", "zipma", "mas1", "mas2",
    "countryinc", "stprinc", "former", "changed", "afs", "wksi", "fye", "form", "period",
    "fy", "fp", "filed", "accepted", "prevrpt", "detail",
    "instance",    # our ticker (KNOWN-BUG(TICKER-GUESS): derived from the XBRL instance filename)
    "nciks", "aciks",
    "edgar_year",  # data set quarter this row was loaded from, e.g. "2009Q4"
)
