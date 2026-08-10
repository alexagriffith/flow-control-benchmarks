# Derivation note — burst-window figures used in the walkthrough

The walkthrough and docs quote the burster at 82 requests per second against
6 per peer, with mean queue durations 45 / 17.5 / 17.3 ms. Those are
BURST-WINDOW values (the interval where the burster is actually sending), not
full-run averages. summary.csv shows full-run averages (~28.8 / 5.9 rps, mean
queue 85 / 54 ms) because the burster idles for the first ~100 s of the run.
Both are correct; quote the burst-window values for the fairness claim and say
"served through the burst" when doing so.
