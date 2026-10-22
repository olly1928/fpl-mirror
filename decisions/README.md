# Decision records

Each new weekly recommendation or full build should produce an immutable,
pre-deadline JSON record using `fpl_advisor.py record`, plus the original input
packet. See `model/README.md` and the chosen playbook for commands.

There are no invented historical forecasts in this directory. Installing the
model does not imply any FPL action was taken. Records initially describe advice
only. If actual execution is later confirmed, retain the original advice and
record the confirmation separately instead of silently changing the prediction.

A Claude sandbox can create records and attach them without authentication. It
cannot automatically commit them through public read-only curl URLs. Retain the
attachment or use an already-authorised GitHub workflow to add the JSON here.
Do not claim a local file was uploaded or will be available in a future session.
