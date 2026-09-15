# Text-filter samples

These files were supplied with the early text-filter modules and are retained as
hands-on inputs outside the installable package source. Try them with commands
such as:

```bash
python -m axlib.tf comments examples/text/text.test
python -m axlib.tf whitespace-safe examples/text/text.yaml
```

Review each transformation before using the result as input to a deployment
system. The filters intentionally operate on text and do not validate a network
vendor's configuration grammar.
