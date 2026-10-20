# Show-command parsing samples

Two collections from two Cisco IOS-XE switches, taken before and after a
change, and one template of our own. `docs/PARSING.md` explains every option;
the tests in `tests/test_parse_*.py` check these files, so they stay correct.

```text
before/sw1.log   session log with prompts: CDP, LLDP (brief and detail),
                 show ip int br, show int status, show ip pim neighbor
after/sw1.log    the same commands, partly abbreviated (sh cdp nei), with
                 neighbors in another order, new hold times, and real changes
before/sw2.txt   a script's output with bare commands and no prompts
after/sw2.txt    the same neighbors in another order with new hold times
templates/       cisco_ios_show_ip_pim_neighbor.textfsm, which ntc-templates
                 lacks, as an example of an operator template
```

Run these from this directory after `uv add 'axlib[textfsm]'`:

```bash
# How each capture is cut into commands:
axlib parse split before/sw1.log

# One collection as sorted tables:
axlib parse -p cisco_ios -t templates before -f table

# What changed (exit status 1 because something did):
axlib parse diff -p cisco_ios -t templates before after

# Compare with a plain text diff of the same switch:
diff -u before/sw1.log after/sw1.log

# Which template parses an abbreviated command, and from where:
axlib parse templates -p cisco_ios -t templates sh ip pim nei
```

What changed in `sw1`: the access point on Gi1/0/5 went away (its port is now
`notconnect`), the router uplink on Gi1/0/48 moved from the router's Gi0/0/1
to Gi0/0/2, and PIM neighbor 10.20.0.2 is no longer the designated router.
`sw2` has no real changes, and `axlib parse diff` doesn't list it.

The device names, addresses, and serial numbers are invented; the addresses
come from documentation and private ranges.
