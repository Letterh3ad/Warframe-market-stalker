# Maket Hunter

A little tool that stalks [Warframe Market](https://warframe.market) prices so you don't have to alt-tab mid-mission.

Point it at an item (or a whole list of mods), and it snapshots the current buy/sell orders into a local database. Run it a few times over a few days and you've got a price history you can plot — enough to spot when a mod is actually worth selling, or when someone's dumping cheap copies.

## How it works

1. **Fetch** — hits the Warframe Market v2 API for an item's top buy/sell orders.
2. **Crunch** — computes min/max/avg/percentiles for both sides, plus spread and "undercut pressure" (how hard people are racing each other to the bottom).
3. **Store** — saves the snapshot as a row in a local SQLite database (`wfm_market.db`).
4. **Plot** — later, pull up the price history for any item as a matplotlib chart.

Item names are fuzzy-matched to their API slugs automatically (typos included), and results are cached locally so it's not re-downloading the full item list every run.

## Setup

```bash
pip install requests matplotlib
```

## Usage

```bash
# Fetch a single item by name (fuzzy matched)
python maket_hunter.py fetch "archon continuity"

# ...or by exact slug, skipping the lookup
python maket_hunter.py fetch archon_continuity --slug

# Fetch a whole list of items from a text file (one per line)
python maket_hunter.py fetch-file mods.txt --slug

# Plot price history for an item
python maket_hunter.py plot archon_continuity
python maket_hunter.py plot archon_continuity --days 7

# Force-refresh the local item cache
python maket_hunter.py refresh-items --force
```

Run `fetch` (or `fetch-file`) on a schedule — cron, Task Scheduler, whatever — to build up a real history over time.
